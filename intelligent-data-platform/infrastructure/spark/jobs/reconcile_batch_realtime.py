"""Sprint 3：批流交叉对账作业（本项目最核心的一次验证）。

运行方式（服务器上，仓库根目录）：
    bash scripts/reconcile-batch-realtime.sh

它做什么：
    1. 从 Doris 通过 JDBC 读实时链路产出的 ecommerce.ads_realtime_trade_1m；
    2. 从湖仓读离线链路产出的 lakehouse.ads_batch_trade_1m；
    3. 计算「两侧都已封闭」的对账区间（尾部留安全边界）；
    4. 逐窗口比对 8 个可加指标 + 窗口覆盖情况，把差异全部落盘到
       lakehouse.ads_reconcile_trade_1m / ads_reconcile_summary；
    5. **差异不为 0 就以非 0 退出**。

为什么尾部要留安全边界：
    实时链路的最后一个窗口可能仍在接收迟到事件（Kafka 里还有未消费的偏移），
    此时两侧不一致是"正常现象"而不是缺陷。若不做边界处理，
    对账会周期性假警报，久而久之就没人看这个结果了 —— 这正是
    数据治理里最常见的失效方式。边界值由作业写入汇总表，可复核。

为什么对账区间不写死：
    数据会重新生成（Sprint 0 的生成器可重跑），写死日期的对账脚本
    在数据一变就永久失败，只能靠改脚本"修绿"，属于自欺欺人。
    这里改为按两侧实际数据范围动态求交。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

from _common import Checker, build_spark, ensure_tables, run_sql_file

# 对账区间尾部安全边界：
#   实时链路的水位线延迟 + 一个窗口的完整性，留 3 分钟足够。
TAIL_GUARD_MINUTES = 3
# 对账覆盖率的硬下限：区间内实时侧窗口数不得少于离线侧的该比例。
#   设这个阈值是为了防"区间取空导致 0 个窗口比对也判通过"这种假成功。
MIN_REALTIME_COVERAGE = 0.9

DORIS_JDBC_DRIVER = "com.mysql.cj.jdbc.Driver"


def doris_options(spark) -> dict[str, str]:
    """Doris 连接参数（用户/口令由提交脚本通过 --conf 注入）。

    !! sessionVariables 不能省 !!
        Doris 通过 MySQL 协议返回 datetime 时会受 session time_zone 影响。
        Spark 的 session 时区是 Asia/Shanghai（见 spark-defaults.conf），
        若 Doris 侧用默认 UTC，读出来的 window_start 会整体偏移 8 小时，
        对账把"时区问题"误报成"指标不一致"，极难排查。
    """
    conf = spark.sparkContext.getConf()
    host = conf.get("spark.doris.host", "doris-fe")
    port = conf.get("spark.doris.queryPort", "9030")
    database = conf.get("spark.doris.database", "ecommerce")
    return {
        "url": (
            f"jdbc:mysql://{host}:{port}/{database}"
            "?useSSL=false&allowPublicKeyRetrieval=true&characterEncoding=UTF-8"
            "&serverTimezone=Asia/Shanghai&sessionVariables=time_zone='%2B08:00'"
        ),
        "driver": DORIS_JDBC_DRIVER,
        "user": conf.get("spark.doris.user", "agent_ro"),
        "password": conf.get("spark.doris.password", ""),
    }


def load_realtime(spark, options: dict[str, str]):
    """读实时交易指标表，并注册为临时视图 v_realtime_trade_src。"""
    df = (
        spark.read.format("jdbc")
        .option("url", options["url"])
        .option("dbtable", "ads_realtime_trade_1m")
        .option("driver", options["driver"])
        .option("user", options["user"])
        .option("password", options["password"])
        .option("fetchsize", "2000")
        .load()
    )
    # 只保留对账需要的列，避免 Doris 表后续加列时 Spark 侧 schema 漂移
    columns = (
        "window_start",
        "gmv",
        "order_cnt",
        "order_user_cnt",
        "payment_cnt",
        "payment_amount",
        "payment_fail_cnt",
        "refund_cnt",
        "refund_amount",
    )
    df = df.select(*columns)
    df.createOrReplaceTempView("v_realtime_trade_src")
    return df.count()


def compute_scope(spark) -> tuple[datetime, datetime, datetime]:
    """计算对账区间：两侧数据范围求交，尾部留安全边界。"""
    realtime = spark.sql(
        "SELECT MIN(window_start) AS lo, MAX(window_start) AS hi FROM v_realtime_trade_src"
    ).first()
    batch = spark.sql(
        "SELECT MIN(window_start) AS lo, MAX(window_start) AS hi FROM lakehouse.ads_batch_trade_1m"
    ).first()

    if realtime["lo"] is None or batch["lo"] is None:
        raise RuntimeError("实时或离线指标表为空，无法对账")

    start = max(realtime["lo"], batch["lo"])
    # hi 是"已出现过的最大窗口起点"，加 1 分钟才是窗口结束；
    # 再减安全边界，确保这一分钟两侧都已封窗。
    end = min(realtime["hi"], batch["hi"]) + timedelta(minutes=1) - timedelta(minutes=TAIL_GUARD_MINUTES)
    if end <= start:
        raise RuntimeError(
            f"对账区间为空：start={start} end={end}；"
            f"实时范围 [{realtime['lo']}, {realtime['hi']}]，"
            f"离线范围 [{batch['lo']}, {batch['hi']}]"
        )
    return start, end, datetime.now()


def main() -> int:
    spark = build_spark("sprint3-reconcile-batch-realtime")

    print("=" * 60, flush=True)
    print(" Sprint 3 — 批流交叉对账（实时 Flink/Doris  vs  离线 Spark/湖仓）", flush=True)
    print("=" * 60, flush=True)

    ensure_tables(spark)

    options = doris_options(spark)
    print(f"[jdbc] 读取实时指标：{options['url'].split('?')[0]} (user={options['user']})", flush=True)
    realtime_rows = load_realtime(spark, options)
    print(f"[jdbc]   ads_realtime_trade_1m → {realtime_rows} 行", flush=True)

    batch_rows = int(
        spark.sql("SELECT COUNT(*) AS c FROM lakehouse.ads_batch_trade_1m").first()["c"]
    )
    print(f"[lake]   ads_batch_trade_1m → {batch_rows} 行", flush=True)

    scope_start, scope_end, compared_at = compute_scope(spark)
    batch_id = compared_at.strftime("reconcile_%Y%m%d_%H%M%S")
    print(
        f"[scope]  对账区间 [{scope_start} , {scope_end})  批次 {batch_id}",
        flush=True,
    )

    run_sql_file(
        spark,
        "04_reconcile.sql",
        variables={
            "SCOPE_START": scope_start.strftime("%Y-%m-%d %H:%M:%S"),
            "SCOPE_END": scope_end.strftime("%Y-%m-%d %H:%M:%S"),
            "COMPARED_AT": compared_at.strftime("%Y-%m-%d %H:%M:%S"),
            "BATCH_ID": batch_id,
        },
    )

    # ------------------------------------------------------------
    # 汇总与断言
    # ------------------------------------------------------------
    summary = spark.sql(
        """
        SELECT matched_windows, mismatched_windows, realtime_windows, batch_windows,
               first_mismatch_at, realtime_total_gmv, batch_total_gmv, is_pass
        FROM lakehouse.ads_reconcile_summary
        WHERE batch_id = '{}'
        """.format(batch_id)
    ).first()

    checker = Checker("批流对账结论")
    checker.check_true(
        "对账窗口数 > 0（防空区间假通过）",
        int(summary["batch_windows"]) > 0,
        "batch_windows 必须大于 0",
    )
    coverage_ok = int(summary["realtime_windows"]) >= int(summary["batch_windows"]) * MIN_REALTIME_COVERAGE
    checker.check_true(
        f"实时侧覆盖率 >= {MIN_REALTIME_COVERAGE:.0%}",
        coverage_ok,
        f"实时 {summary['realtime_windows']} 行 vs 离线 {summary['batch_windows']} 行",
    )
    checker.check("不一致窗口数", int(summary["mismatched_windows"]), 0)
    checker.check("实时 GMV 合计 == 离线 GMV 合计", summary["batch_total_gmv"], summary["realtime_total_gmv"])
    checker.check_true("总体结论 is_pass", bool(summary["is_pass"]), f"首个不一致窗口 {summary['first_mismatch_at']}")

    print("", flush=True)
    print(
        f"[scope]  对账窗口 {summary['batch_windows']} 个"
        f"（实时侧 {summary['realtime_windows']} 个），"
        f"一致 {summary['matched_windows']} 个，不一致 {summary['mismatched_windows']} 个",
        flush=True,
    )
    print(
        f"[scope]  GMV 合计：实时 {summary['realtime_total_gmv']} vs 离线 {summary['batch_total_gmv']}",
        flush=True,
    )

    if int(summary["mismatched_windows"]) > 0:
        print("", flush=True)
        print("[diff ] 前 10 个不一致窗口：", flush=True)
        for row in spark.sql(
            """
            SELECT window_start, diff_gmv, diff_order_cnt, diff_order_user_cnt,
                   diff_payment_cnt, diff_payment_amount, diff_payment_fail_cnt,
                   diff_refund_cnt, diff_refund_amount
            FROM lakehouse.ads_reconcile_trade_1m
            WHERE NOT is_match
            ORDER BY window_start
            LIMIT 10
            """
        ).collect():
            print(
                f"[diff ]   {row['window_start']}  Δgmv={row['diff_gmv']}"
                f" Δorder={row['diff_order_cnt']} Δuser={row['diff_order_user_cnt']}"
                f" Δpay={row['diff_payment_cnt']} Δpay_amt={row['diff_payment_amount']}"
                f" Δfail={row['diff_payment_fail_cnt']} Δrefund={row['diff_refund_cnt']}"
                f" Δrefund_amt={row['diff_refund_amount']}",
                flush=True,
            )

    exit_code = checker.finish()
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
