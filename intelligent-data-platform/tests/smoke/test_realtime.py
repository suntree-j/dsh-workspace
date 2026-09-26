"""Sprint 1 实时链路冒烟测试（Kafka → Flink → Doris）。

前置条件：
    cp .env.example .env
    docker compose up -d
    （等待 Flink 集群就绪、Routine Load 建立，首次约 3~5 分钟）

运行：
    python -m pytest -m smoke -v tests/smoke/test_realtime.py

覆盖范围（对应 docs/sprint/SPRINT_1.md 的验收标准）：
    1. Flink 集群可用（JobManager REST + TaskManager slots）
    2. Flink SQL Gateway 可用
    3. 8 个实时作业（4 DWD + 4 指标）在运行
    4. 8 个下游 Topic 存在
    5. 8 张 Doris 数仓表存在且字段完整
    6. 8 个 Routine Load 作业 RUNNING 且无脏数据
    7. DWD 明细与 Kafka / MySQL 逐层对账（含重复投递检查）
    8. ADS 指标与 MySQL 事实表对账（GMV 精确到分）

设计说明（为什么不"立即断言"）：
    链路是异步的 —— Flink 按水位线触发窗口，Routine Load 有秒级延迟。
    因此对账类断言先「限时等待收敛」，再断言最终值，
    避免测试因正常延迟误报失败；但**不会**放宽断言本身。
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Callable, TypeVar

import pytest

pytestmark = pytest.mark.smoke

T = TypeVar("T")

# ------------------------------------------------------------
# 期望的实时链路拓扑（与 infrastructure/flink/sql/、sql/doris/ 一一对应）
# ------------------------------------------------------------
DWD_TOPICS = (
    "dwd_trade_order_detail",
    "dwd_trade_payment_detail",
    "dwd_trade_refund_detail",
    "dwd_traffic_behavior_detail",
)
DWS_TOPICS = ("dws_traffic_overview_1m",)
ADS_TOPICS = (
    "ads_realtime_trade_1m",
    "ads_realtime_traffic_1m",
    "ads_realtime_category_1m",
)
REALTIME_TOPICS = DWD_TOPICS + DWS_TOPICS + ADS_TOPICS
REALTIME_TABLES = REALTIME_TOPICS
ROUTINE_LOADS = tuple(f"rl_{t}" for t in REALTIME_TOPICS)

EXPECTED_FLINK_JOBS = 8          # 4 个 DWD 清洗 + 4 个指标窗口作业
SOURCE_TOPICS = ("order_event", "payment_event", "refund_event", "behavior_event")

KAFKA_BIN = "/opt/kafka/bin"
BOOTSTRAP = "kafka:9092"

# 收敛等待上限：生成事件后，Flink 窗口 + Routine Load 全部落库
CONVERGE_TIMEOUT = 300
CONVERGE_INTERVAL = 6


# ------------------------------------------------------------
# 小工具
# ------------------------------------------------------------
def wait_for(desc: str, probe: Callable[[], T], ok: Callable[[T], bool],
             timeout: int = CONVERGE_TIMEOUT, interval: int = CONVERGE_INTERVAL) -> T:
    """限时等待 probe() 满足 ok()，返回最后一次探测结果。

    注意：超时后**返回**最后一次结果由调用方断言，避免这里吞掉失败原因。
    """
    deadline = time.monotonic() + timeout
    result = probe()
    while not ok(result) and time.monotonic() < deadline:
        time.sleep(interval)
        result = probe()
    return result


def kafka_offsets(docker, topic: str) -> int:
    """取 topic 当前总消息数（各分区 offset 之和）。"""
    out = docker.out(
        "kafka", f"{KAFKA_BIN}/kafka-get-offsets.sh",
        "--bootstrap-server", BOOTSTRAP, "--topic", topic, timeout=60,
    )
    total = 0
    for line in out.splitlines():
        parts = line.strip().split(":")
        if len(parts) == 3 and parts[2].isdigit():
            total += int(parts[2])
    return total


def doris_rows(docker, fe_ip: str, sql: str, timeout: int = 120) -> list[list[str]]:
    """执行 Doris 查询，返回按 tab 拆分的行。"""
    out = docker.out(
        "doris-be", "mysql", "-h", fe_ip, "-P", "9030", "-uroot",
        "--connect-timeout=10", "-N", "-B", "-e", sql, timeout=timeout,
    )
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def doris_scalar(docker, fe_ip: str, sql: str, timeout: int = 120) -> str:
    """执行 Doris 查询并返回单个标量值（字符串）。"""
    rows = doris_rows(docker, fe_ip, sql, timeout=timeout)
    assert rows and rows[0], f"查询无结果：{sql}"
    return rows[0][0].strip()


def mysql_scalar(docker, creds: tuple[str, str], sql: str, timeout: int = 60) -> str:
    """执行 MySQL 查询并返回单个标量值（字符串）。"""
    user, password = creds
    out = docker.out(
        "mysql", "mysql", "-h", "127.0.0.1", "-u", user, f"-p{password}",
        "-N", "-B", "-e", sql, timeout=timeout,
    )
    return out.strip()


def as_decimal(value: str) -> Decimal:
    """把数据库返回的数值字符串转成 Decimal（金额必须精确比较，禁止 float）。"""
    return Decimal(value.strip())


@pytest.fixture(scope="module")
def fe_ip(env: dict[str, str]) -> str:
    return env.get("DORIS_FE_IP", "172.28.0.10")


# ============================================================
# 1~2. Flink 集群 / SQL Gateway
# ============================================================
def test_flink_cluster_reachable(docker, require_realtime: None) -> None:
    """JobManager REST 可用，且至少注册了一个 TaskManager（有 slot）。

    说明：此处是在 flink-jobmanager **容器内部**访问它自己的
    localhost:8081，不属于「容器间通信用 localhost」的禁止范围。
    """
    body = docker.out(
        "flink-jobmanager", "curl", "-fsS", "--max-time", "10",
        "http://localhost:8081/overview",
    )
    data = json.loads(body)

    assert data["taskmanagers"] >= 1, f"没有 TaskManager 注册：{data}"
    assert data["slots-total"] >= 1, f"没有可用 slot：{data}"
    assert data["flink-version"], f"未返回 Flink 版本：{data}"


def test_flink_jobs_running(docker, require_realtime: None) -> None:
    """8 个实时作业必须全部处于 RUNNING。

    作业数不足通常意味着 SQL 提交阶段有语句失败：
    看 `docker logs flink-jobs | grep FAIL`。
    """
    body = docker.out(
        "flink-jobmanager", "curl", "-fsS", "--max-time", "10",
        "http://localhost:8081/overview",
    )
    data = json.loads(body)
    running = int(data["jobs-running"])

    assert running >= EXPECTED_FLINK_JOBS, (
        f"仅 {running}/{EXPECTED_FLINK_JOBS} 个作业在运行。\n"
        "  排查： docker logs flink-jobs | grep -A2 FAIL"
    )


def test_flink_sql_gateway_ready(docker, require_realtime: None) -> None:
    """SQL Gateway REST 接口可用（容器间使用服务名，不用 localhost）。"""
    body = docker.out(
        "flink-jobmanager", "curl", "-fsS", "--max-time", "10",
        "http://flink-sql-gateway:8083/v1/info",
    )
    info = json.loads(body)
    assert info.get("productName"), f"SQL Gateway 返回异常：{info}"


# ============================================================
# 4. 下游 Topic 存在
# ============================================================
@pytest.mark.parametrize("topic", REALTIME_TOPICS)
def test_realtime_topic_exists(docker, require_realtime: None, topic: str) -> None:
    topics = docker.out(
        "kafka", f"{KAFKA_BIN}/kafka-topics.sh",
        "--bootstrap-server", BOOTSTRAP, "--list", timeout=60,
    ).splitlines()
    assert topic in {t.strip() for t in topics}, f"Topic {topic} 不存在"


# ============================================================
# 5. Doris 数仓表存在且字段完整
# ============================================================
@pytest.mark.parametrize("table", REALTIME_TABLES)
def test_realtime_table_exists(docker, require_realtime: None, fe_ip: str,
                               table: str) -> None:
    count = doris_scalar(
        docker, fe_ip,
        "SELECT COUNT(*) FROM information_schema.tables "
        f"WHERE table_schema='ecommerce' AND table_name='{table}';",
    )
    assert count == "1", f"表 ecommerce.{table} 不存在"


@pytest.mark.parametrize("table", REALTIME_TABLES)
def test_realtime_table_has_columns(docker, require_realtime: None, fe_ip: str,
                                    table: str) -> None:
    """每张表至少 5 个字段 —— 防止"建了个空壳表"也算通过。"""
    count = int(doris_scalar(
        docker, fe_ip,
        "SELECT COUNT(*) FROM information_schema.columns "
        f"WHERE table_schema='ecommerce' AND table_name='{table}';",
    ))
    assert count >= 5, f"表 ecommerce.{table} 只有 {count} 个字段，疑似空壳表"


# ============================================================
# 6. Routine Load 作业
# ============================================================
def _routine_load_blocks(docker, fe_ip: str) -> dict[str, dict[str, str]]:
    """解析 SHOW ROUTINE LOAD\\G 输出，返回 {作业名: {字段: 值}}。

    注意三点（都是实测踩出来的）：
      1. 必须带 `USE <db>` —— 否则 Doris 直接报
         `errCode = 2, detailMessage = No database selected`；
      2. 不能加 mysql 的 `-N` 参数 —— 它会去掉 `Name:` / `State:` 这些
         字段标签，解析必然失败（health-check.sh 里踩过同一个坑）；
      3. **没有**顶层的 `LoadedRows:` / `ErrorRows:` 字段 ——
         行数藏在 `Statistic:` 这一个 JSON 字符串里
         （`{"loadedRows":6000,"errorRows":0,...}`），必须再解析一层。
         曾经因为直接取 `info["LoadedRows"]` 而"永远取不到"：
         一个用例白等 300 秒后失败，另一个用例则**假通过**。
    """
    out = docker.out(
        "doris-be", "mysql", "-h", fe_ip, "-P", "9030", "-uroot",
        "--connect-timeout=10", "-B",
        "-e", "USE ecommerce; SHOW ROUTINE LOAD\\G", timeout=120,
    )
    blocks: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in out.splitlines():
        if not raw.strip() or ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "Name":
            current = {}
            blocks[value] = current
        elif current is not None:
            current[key] = value

    # 展开 Statistic JSON，让调用方可以直接取 loadedRows / errorRows
    for info in blocks.values():
        stat = info.get("Statistic", "")
        if stat.startswith("{"):
            try:
                info.update(json.loads(stat))
            except json.JSONDecodeError:
                pass
    return blocks


def _routine_load_rows(info: dict[str, str], field: str) -> int:
    """读取 Routine Load 的行数（来自 Statistic JSON）。"""
    try:
        return int(info.get(field, "0") or 0)
    except ValueError:
        return 0


@pytest.mark.parametrize("job", ROUTINE_LOADS)
def test_routine_load_running(docker, require_realtime: None, fe_ip: str,
                              job: str) -> None:
    """每个 Routine Load 作业都必须存在且 RUNNING。

    这是整条链路最关键的一项：Topic 有数据 ≠ Doris 有数据。
    作业一旦 PAUSED，表现就是"看起来在跑、实际断流"。
    """
    blocks = _routine_load_blocks(docker, fe_ip)
    assert job in blocks, (
        f"Routine Load {job} 不存在。\n"
        "  检查 doris-be 启动日志中的 13_routine_load.sh 执行结果"
    )
    assert blocks[job].get("State") == "RUNNING", (
        f"{job} 状态为 {blocks[job].get('State')}，"
        f"原因：{blocks[job].get('ReasonOfStateChanged')}"
    )


def test_routine_load_no_error_rows(docker, require_realtime: None, fe_ip: str) -> None:
    """所有 Routine Load 作业的 errorRows 必须为 0（不允许脏数据长期存在）。"""
    blocks = _routine_load_blocks(docker, fe_ip)
    bad = {
        name: _routine_load_rows(info, "errorRows")
        for name, info in blocks.items()
        if name in ROUTINE_LOADS and _routine_load_rows(info, "errorRows") != 0
    }
    assert not bad, f"存在错误行：{bad}"


def test_routine_load_loaded_rows_positive(docker, require_realtime: None,
                                           fe_ip: str) -> None:
    """作业 RUNNING 之外，必须确实有数据被写入（防止"空转"）。

    为什么要「限时等待」而不是立即断言：
        作业刚提交时 Routine Load 可能还没消费到数据（窗口要等水位线推进），
        某个 topic 的 LoadedRows 短暂为 0 属于正常现象。
        这里等待数据真正流进来，超时才算失败 —— 断言本身不放宽。
    """
    def probe() -> list[str]:
        blocks = _routine_load_blocks(docker, fe_ip)
        return [
            name for name in ROUTINE_LOADS
            if _routine_load_rows(blocks.get(name, {}), "loadedRows") <= 0
        ]

    empty = wait_for("所有 Routine Load 都加载到数据", probe, lambda names: not names)

    assert not empty, (
        f"等待 {CONVERGE_TIMEOUT}s 后仍未加载任何数据：{empty}\n"
        "  检查 Kafka 是否有数据： docker exec kafka "
        f"{KAFKA_BIN}/kafka-get-offsets.sh --bootstrap-server {BOOTSTRAP} --topic <topic>"
    )


# ============================================================
# 7. DWD 明细对账
# ============================================================
DWD_EXPECTATIONS = (
    # (Doris 表, Doris key 列, MySQL 事实表计数 SQL, 说明)
    ("dwd_trade_order_detail", "order_id",
     "SELECT COUNT(*) FROM ecommerce.orders", "订单"),
    ("dwd_trade_payment_detail", "payment_id",
     "SELECT COUNT(*) FROM ecommerce.payment", "支付"),
    ("dwd_trade_refund_detail", "refund_id",
     "SELECT COUNT(*) FROM ecommerce.refund", "退款"),
)


@pytest.mark.parametrize("table,key_col,mysql_sql,label", DWD_EXPECTATIONS)
def test_dwd_matches_mysql_source(docker, require_realtime: None, mysql_creds,
                                  fe_ip: str, table: str, key_col: str,
                                  mysql_sql: str, label: str) -> None:
    """DWD 明细行数必须等于 MySQL 事实表行数（MySQL 是唯一事实源）。

    同时校验 COUNT(*) == COUNT(DISTINCT key)：
    Doris 用业务主键做 UNIQUE KEY，若 Kafka 重复投递 / 消费位点回退，
    这一步会暴露"重复数据被静默覆盖成一条"以外的异常。
    """
    expected = int(mysql_scalar(docker, mysql_creds, mysql_sql))

    def probe() -> tuple[int, int]:
        row = doris_rows(
            docker, fe_ip,
            f"SELECT COUNT(*), COUNT(DISTINCT {key_col}) FROM ecommerce.{table};",
        )[0]
        return int(row[0]), int(row[1])

    total, distinct = wait_for(
        f"{table} 行数收敛到 {expected}",
        probe,
        lambda r: r[0] == expected,
    )

    assert total == expected, (
        f"{label}明细不一致：Doris {table}={total}，MySQL={expected}\n"
        "  排查： SHOW ROUTINE LOAD FOR rl_" + table
    )
    assert distinct == total, f"{table} 存在重复主键：COUNT(*)={total}, DISTINCT={distinct}"


def test_dwd_behavior_matches_kafka(docker, require_realtime: None, fe_ip: str) -> None:
    """行为明细行数必须等于 behavior_event 事件数。"""
    expected = kafka_offsets(docker, "behavior_event")
    assert expected > 0, "behavior_event 没有数据，请先运行数据生成器"

    total = int(wait_for(
        "行为明细收敛",
        lambda: int(doris_scalar(
            docker, fe_ip, "SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail;")),
        lambda n: n == expected,
    ))
    assert total == expected, f"行为明细 {total} 条，Kafka 事件 {expected} 条"


def test_dwd_order_amount_matches_mysql(docker, require_realtime: None, mysql_creds,
                                        fe_ip: str) -> None:
    """金额必须逐分一致（禁止 float 误差）。"""
    expected = as_decimal(mysql_scalar(
        docker, mysql_creds, "SELECT SUM(amount) FROM ecommerce.orders"))
    actual = as_decimal(doris_scalar(
        docker, fe_ip, "SELECT SUM(amount) FROM ecommerce.dwd_trade_order_detail;"))
    assert actual == expected, f"DWD 订单金额 {actual} != MySQL {expected}"


# ============================================================
# 8. ADS 指标对账（GMV 精确到分）
# ============================================================
def test_ads_trade_reconciles_with_mysql(docker, require_realtime: None, mysql_creds,
                                         fe_ip: str) -> None:
    """ADS 实时交易总览必须与 MySQL 事实表完全对账。

    对账方式：把 1 分钟窗口的指标按窗口求和，应回到全局口径 ——
      SUM(order_cnt)    = MySQL 订单数
      SUM(gmv)          = MySQL 订单金额合计（精确到分）
      SUM(payment_cnt)  = MySQL 支付成功笔数
      SUM(refund_cnt)   = MySQL 退款笔数

    这条断言同时验证了「窄表 UNION ALL → 条件聚合透视成宽表」
    的实现没有丢失任何指标（曾经因列顺序不一致导致作业提交失败）。
    """
    exp_orders = int(mysql_scalar(
        docker, mysql_creds, "SELECT COUNT(*) FROM ecommerce.orders"))
    exp_gmv = as_decimal(mysql_scalar(
        docker, mysql_creds, "SELECT SUM(amount) FROM ecommerce.orders"))
    exp_pay = int(mysql_scalar(
        docker, mysql_creds,
        "SELECT COUNT(*) FROM ecommerce.payment WHERE payment_status='SUCCESS'"))
    exp_refund = int(mysql_scalar(
        docker, mysql_creds, "SELECT COUNT(*) FROM ecommerce.refund"))

    def probe() -> tuple[int, Decimal, int, int, int]:
        row = doris_rows(
            docker, fe_ip,
            "SELECT COUNT(*), CAST(SUM(order_cnt) AS BIGINT), SUM(gmv), "
            "CAST(SUM(payment_cnt) AS BIGINT), CAST(SUM(refund_cnt) AS BIGINT) "
            "FROM ecommerce.ads_realtime_trade_1m;",
        )[0]
        return (int(row[0]), as_decimal(row[1]), as_decimal(row[2]),
                int(row[3]), int(row[4]))

    windows, orders, gmv, pay, refund = wait_for(
        "ADS 交易指标收敛",
        probe,
        lambda r: r[1] == exp_orders and r[2] == exp_gmv and r[3] == exp_pay,
    )

    assert windows > 0, "ads_realtime_trade_1m 没有窗口数据"
    assert orders == exp_orders, f"订单量不一致：ADS {orders} vs MySQL {exp_orders}"
    assert gmv == exp_gmv, f"GMV 不一致：ADS {gmv} vs MySQL {exp_gmv}"
    assert pay == exp_pay, f"支付笔数不一致：ADS {pay} vs MySQL {exp_pay}"
    assert refund == exp_refund, f"退款笔数不一致：ADS {refund} vs MySQL {exp_refund}"


def test_ads_trade_additive_metrics_not_null(docker, require_realtime: None,
                                             fe_ip: str) -> None:
    """可加指标必须补 0 而不是留 NULL（见 sql/metadata/metrics.md 第 2.1 节）。

    典型场景：某分钟只有下单、没有支付 —— 该窗口 payment_cnt 应为 0。
    """
    row = doris_rows(
        docker, fe_ip,
        "SELECT SUM(order_cnt IS NULL), SUM(payment_cnt IS NULL), "
        "SUM(refund_cnt IS NULL), SUM(gmv IS NULL) "
        "FROM ecommerce.ads_realtime_trade_1m;",
    )[0]
    nulls = [int(x) for x in row]
    assert nulls == [0, 0, 0, 0], (
        f"可加指标存在 NULL 值（order_cnt/payment_cnt/refund_cnt/gmv = {nulls}）"
    )


def test_ads_traffic_reconciles_with_kafka(docker, require_realtime: None,
                                           fe_ip: str) -> None:
    """流量指标必须与行为事件总数对账。"""
    expected = kafka_offsets(docker, "behavior_event")
    assert expected > 0, "behavior_event 没有数据，请先运行数据生成器"

    def probe() -> tuple[int, int, int]:
        row = doris_rows(
            docker, fe_ip,
            "SELECT CAST(SUM(pv) AS BIGINT), CAST(SUM(view_cnt) AS BIGINT), "
            "CAST(SUM(buy_cnt) AS BIGINT) FROM ecommerce.ads_realtime_traffic_1m;",
        )[0]
        return int(row[0]), int(row[1]), int(row[2])

    pv, view, buy = wait_for("ADS 流量指标收敛", probe, lambda r: r[0] == expected)

    assert pv == expected, f"PV 合计 {pv} != 行为事件数 {expected}"
    assert 0 < buy <= view <= pv, (
        f"漏斗关系不成立：pv={pv}, view={view}, buy={buy}（应满足 buy <= view <= pv）"
    )


def test_ads_traffic_dws_consistent(docker, require_realtime: None, fe_ip: str) -> None:
    """ADS 流量与 DWS 流量必须一致（ADS 由同一套窗口聚合产出）。"""
    ads = int(doris_scalar(
        docker, fe_ip, "SELECT CAST(SUM(pv) AS BIGINT) FROM ecommerce.ads_realtime_traffic_1m;"))
    dws = int(doris_scalar(
        docker, fe_ip, "SELECT CAST(SUM(pv) AS BIGINT) FROM ecommerce.dws_traffic_overview_1m;"))
    assert ads == dws, f"ADS PV 合计 {ads} != DWS PV 合计 {dws}"


def test_ads_category_reconciles_with_mysql(docker, require_realtime: None,
                                            mysql_creds, fe_ip: str) -> None:
    """类目销售指标必须与订单事实对账（窗口 × 类目不应丢单）。"""
    exp_orders = int(mysql_scalar(
        docker, mysql_creds, "SELECT COUNT(*) FROM ecommerce.orders"))
    exp_gmv = as_decimal(mysql_scalar(
        docker, mysql_creds, "SELECT SUM(amount) FROM ecommerce.orders"))

    def probe() -> tuple[int, int, Decimal]:
        row = doris_rows(
            docker, fe_ip,
            "SELECT COUNT(*), CAST(SUM(order_cnt) AS BIGINT), SUM(gmv) "
            "FROM ecommerce.ads_realtime_category_1m;",
        )[0]
        return int(row[0]), int(row[1]), as_decimal(row[2])

    windows, orders, gmv = wait_for(
        "ADS 类目指标收敛", probe, lambda r: r[1] == exp_orders and r[2] == exp_gmv)

    assert windows > 0, "ads_realtime_category_1m 没有数据（类目作业可能未提交成功）"
    assert orders == exp_orders, f"类目订单数合计 {orders} != MySQL {exp_orders}"
    assert gmv == exp_gmv, f"类目 GMV 合计 {gmv} != MySQL {exp_gmv}"
