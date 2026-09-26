"""Sprint 2 离线链路冒烟测试（MySQL → Spark → Parquet(S3A) → Hive 外部表）。

前置条件（服务器上）：
    bash scripts/init-lakehouse.sh      # 建 Metastore、起 Spark、注册 ODS 表
    bash scripts/submit-offline-job.sh  # 抽取数据（幂等，可重复跑）

运行：
    python -m pytest -m smoke -v tests/smoke/test_offline.py

覆盖范围（对应 docs/sprint/SPRINT_2.md 第 5 节）：
    1. 三个容器（metastore / master / worker）健康
    2. Spark 集群有 Worker 注册且提供资源
    3. 5 张 ODS 表都在 lakehouse 库，且都是**外部表**
    4. **逐表行数与 MySQL 精确一致** —— 离线链路的核心价值是"能对账"
    5. Parquet 真的落在 MinIO（不是容器本地盘）
    6. 金额字段是 decimal 而不是 double（精度风险）

设计说明：
    查询走 `bash scripts/spark-sql.sh`（一次性容器），避免在 spark-master
    里起 driver 顶破它的内存上限；服务未就绪时优雅跳过（AGENTS.md 第 8.3 节）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parents[2]

# (湖仓 ODS 表, MySQL 源表)
ODS_PAIRS = [
    ("ods_user", "user"),
    ("ods_product", "product"),
    ("ods_orders", "orders"),
    ("ods_payment", "payment"),
    ("ods_refund", "refund"),
]

OFFLINE_CONTAINERS = ("hive-metastore", "spark-master", "spark-worker")


# ------------------------------------------------------------
# 小工具
# ------------------------------------------------------------
def as_int(value: str) -> int:
    """把 Spark/MySQL 的输出转成 int，写错时给出清晰报错。"""
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise AssertionError(f"期望整数，实际拿到 {value!r}") from exc


def spark_sql(sql: str, timeout: int = 240) -> str:
    """执行 Spark SQL，返回去掉日志与计时行的结果文本。"""
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "spark-sql.sh"), "-e", sql],
        capture_output=True, text=True, timeout=timeout, check=False, errors="replace",
    )
    assert result.returncode == 0, (
        f"Spark SQL 执行失败（exit {result.returncode}）\n{result.stderr[-1500:]}"
    )
    lines = [
        line for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("Time taken")
    ]
    return "\n".join(lines)


def spark_scalar(sql: str) -> str:
    lines = spark_sql(sql).strip().splitlines()
    assert lines, f"Spark SQL 无输出：{sql}"
    return lines[0].strip()


@pytest.fixture(scope="module", autouse=True)
def require_offline_stack(docker) -> None:
    """离线链路容器必须都在运行，否则跳过本模块。"""
    missing = [name for name in OFFLINE_CONTAINERS if not docker.running(name)]
    if missing:
        pytest.skip(
            f"离线链路容器未运行：{', '.join(missing)}\n"
            "  请先执行： bash scripts/init-lakehouse.sh"
        )


# ============================================================
# 1. 容器健康
# ============================================================
@pytest.mark.parametrize("container", OFFLINE_CONTAINERS)
def test_container_healthy(docker, container: str) -> None:
    status = docker.health(container)
    assert status == "healthy", (
        f"{container} 健康状态为 {status}；排查： docker compose logs --tail=50 {container}"
    )


# ============================================================
# 2. Spark 集群
# ============================================================
def test_spark_cluster_has_worker(docker) -> None:
    """集群必须至少有一个已注册的 Worker，且容量足够跑作业。

    !! 必须用服务名而不是 localhost（同一个坑踩了两次）!!
        Spark 的 MasterUI 绑定在"容器主机名对应的地址"上，
        容器内 `curl http://localhost:8080/` 会被拒：
            curl: (7) Failed to connect to localhost port 8080: Connection refused
        （docker-compose 的 healthcheck 早就为此改成 `http://spark-master:8080/`，
          注释里也写了原因，但测试里又写回了 localhost。）
        服务名在 data-platform 网络里可解析，是唯一可靠的地址。

    !! 为什么不断言"当前空闲"!!
        /json/ 里的 coresused / memoryused 会随作业波动。
        若断言"必须有空闲 core"，集群正在跑作业时会随机失败 ——
        而"忙"是完全正常的状态。这里只断言结构性事实。
    """
    result = docker.run("spark-master", "curl", "-fsS", "http://spark-master:8080/json/")
    assert result.returncode == 0, "Spark Master Web UI 无响应"
    info = json.loads(result.stdout)

    assert int(info["aliveworkers"]) >= 1, f"没有 Worker 注册：{info}"
    assert int(info["cores"]) >= 1, f"Worker 总 core 数为 0：{info}"
    assert int(info["memory"]) >= 1024, f"Worker 总内存过小：{info}"

    # 账目自洽：已用不可能超过总量（能抓住 Worker 状态异常）
    cores_used = int(info.get("coresused", 0))
    memory_used = int(info.get("memoryused", 0))
    assert 0 <= cores_used <= int(info["cores"]), f"core 账目异常：{info}"
    assert 0 <= memory_used <= int(info["memory"]), f"内存账目异常：{info}"


# ============================================================
# 3. ODS 表存在且是外部表
# ============================================================
def test_ods_tables_exist() -> None:
    tables = spark_sql("SHOW TABLES IN lakehouse;")
    missing = [ods for ods, _ in ODS_PAIRS if ods not in tables]
    assert not missing, f"缺少 ODS 表：{missing}；实际：{tables}"


@pytest.mark.parametrize("ods,mysql_table", ODS_PAIRS)
def test_ods_table_is_external(ods: str, mysql_table: str) -> None:
    """必须是外部表：DROP TABLE 只删元数据，不会连数据一起删掉。

    !! 为什么判 LOCATION 而不是找 "EXTERNAL" 关键字（实测踩坑）!!
        Spark 的 `SHOW CREATE TABLE` **不会**输出 EXTERNAL 关键字，
        实测（Sprint 3 验收）输出的 DDL 里
            EXTERNAL 出现 0 次、LOCATION 出现 1 次。
        而"建表时显式给了 LOCATION"在 Hive/Spark 的 catalog 语义里
        正是外部表的判定条件（未指定 LOCATION 的表才是 managed table）。
        原断言找 EXTERNAL，会稳定失败 —— 那是断言写错，不是表建错了。
    """
    ddl = spark_sql(f"SHOW CREATE TABLE lakehouse.{ods};")
    upper = ddl.upper()
    assert "LOCATION" in upper, f"{ods} 的 DDL 没有 LOCATION，可能是 managed table：{ddl[:300]}"
    assert "S3A://LAKEHOUSE/WAREHOUSE/ODS/" in upper, (
        f"{ods} 的数据位置不在湖仓 ODS 路径下：{ddl[:300]}"
    )


# ============================================================
# 4. 逐表行数对账（核心断言）
# ============================================================
@pytest.mark.parametrize("ods,mysql_table", ODS_PAIRS)
def test_row_count_matches_mysql(docker, mysql_creds, ods: str, mysql_table: str) -> None:
    user, password = mysql_creds
    mysql_count = as_int(docker.out(
        "mysql", "mysql", "-h", "127.0.0.1", "-u", user, f"-p{password}", "-N", "-B",
        "-e", f"SELECT COUNT(*) FROM ecommerce.{mysql_table}",
    ))
    lake_count = as_int(spark_scalar(f"SELECT COUNT(*) FROM lakehouse.{ods};"))

    assert lake_count == mysql_count, (
        f"{ods} 行数与 MySQL 不一致：湖仓 {lake_count} vs MySQL {mysql_count}\n"
        "  离线链路的价值就在于能对账；不一致必须查原因，不能放宽断言"
    )


# ============================================================
# 5. 存储落地：Parquet 在 MinIO
# ============================================================
def test_parquet_files_land_in_minio(docker, env) -> None:
    user = env.get("MINIO_ROOT_USER", "")
    password = env.get("MINIO_ROOT_PASSWORD", "")
    assert user and password, "缺少 MinIO 凭据（检查 .env）"

    out = docker.out(
        "minio", "sh", "-c",
        f"MC_HOST_local='http://{user}:{password}@localhost:9000'; export MC_HOST_local; "
        "mc ls --recursive local/lakehouse/warehouse/ods | head -20",
        timeout=60,
    )
    assert ".parquet" in out, f"MinIO 的 ods 目录下没有 Parquet 文件：{out[:400]}"


# ============================================================
# 6. 类型正确性
# ============================================================
def test_money_columns_are_decimal() -> None:
    schema = spark_sql("DESCRIBE lakehouse.ods_orders;")
    amount_lines = [line for line in schema.splitlines() if "amount" in line]
    assert amount_lines, f"ods_orders 没有 amount 字段：{schema}"
    assert any("decimal(18,2)" in line for line in amount_lines), (
        f"amount 必须是 decimal(18,2)，用 double 会破坏精确对账：{amount_lines}"
    )


@pytest.mark.parametrize("ods,mysql_table", ODS_PAIRS)
def test_no_float_columns_in_ods(ods: str, mysql_table: str) -> None:
    schema = spark_sql(f"DESCRIBE lakehouse.{ods};")
    bad = [line for line in schema.splitlines() if "double" in line or "float" in line]
    assert not bad, f"{ods} 含浮点字段：{bad}"
