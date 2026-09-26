"""Sprint 3：DWD → ADS（指标层）构建作业。

运行方式（服务器上，仓库根目录）：
    bash scripts/submit-offline-job.sh --stage ads

它做什么：
    1. 确保 ADS 表存在（sql/hive/04_ads_tables.sql）；
    2. 执行 infrastructure/spark/sql/03_ads_metrics.sql：
       按 sql/metadata/metrics.md 的口径算出与实时链路**同名同粒度**的指标；
    3. **作业内自检**：
       - 1 分钟指标表的窗口数 == 三条事件流出现过的不同分钟数；
       - GMV / 支付金额 / 退款金额 等可加量必须与 DWD 精确相等（到分）；
       - 天粒度表必须等于分钟粒度表按天上卷（保证"天 = 分钟之和"）。

为什么这几条断言是"批流对账"的前提：
    如果离线链路自己内部都自相矛盾（分钟表和天表对不上、
    或者类目表与订单表总额不等），那么与实时链路对账失败时
    根本无法判断是"离线算错"还是"实时算错"。
    先把离线链路内部闭合，再去比实时，问题才可定位。
"""

from __future__ import annotations

import sys

from _common import Checker, build_spark, ensure_tables, exact_distinct, run_sql_file

# 与 sql/doris/12_ads_tables.sql 的 ads_realtime_trade_1m 必须逐字段一致
REALTIME_TRADE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("window_start", "timestamp"),
    ("window_end", "timestamp"),
    ("gmv", "decimal(18,2)"),
    ("order_cnt", "bigint"),
    ("order_user_cnt", "bigint"),
    ("avg_order_amount", "decimal(18,2)"),
    ("payment_cnt", "bigint"),
    ("payment_amount", "decimal(18,2)"),
    ("payment_fail_cnt", "bigint"),
    ("payment_success_rate", "decimal(10,4)"),
    ("refund_cnt", "bigint"),
    ("refund_amount", "decimal(18,2)"),
    ("refund_rate", "decimal(10,4)"),
)


def scalar(spark, sql: str):
    return spark.sql(sql).first()[0]


def main() -> int:
    spark = build_spark("sprint3-build-ads")

    print("=" * 60, flush=True)
    print(" Sprint 3 — DWD → ADS 指标计算", flush=True)
    print("=" * 60, flush=True)

    ensure_tables(spark)
    run_sql_file(spark, "03_ads_metrics.sql")

    checker = Checker("ADS 层自检")

    # ---- 1. 分钟窗口数 == 事件出现过的分钟数 ----
    #
    # !! 这里必须用精确去重 !!
    #   COUNT(DISTINCT w) 在 Spark 里是近似算法（约 5% 误差），
    #   拿它当"窗口数必须相等"的断言会随机通过/失败（实测踩坑，详见 _common.exact_distinct）。
    expected_windows = len(
        spark.sql(
            """
            SELECT DISTINCT w FROM (
                SELECT DATE_TRUNC('minute', event_time) AS w FROM lakehouse.dwd_trade_order_detail
                UNION
                SELECT DATE_TRUNC('minute', event_time) AS w FROM lakehouse.dwd_trade_payment_detail
                UNION
                SELECT DATE_TRUNC('minute', event_time) AS w FROM lakehouse.dwd_trade_refund_detail
            ) t
            """
        ).collect()
    )
    actual_windows = int(scalar(spark, "SELECT COUNT(*) FROM lakehouse.ads_batch_trade_1m"))
    checker.check("ads_batch_trade_1m 窗口数", actual_windows, expected_windows)

    # ---- 2. 可加指标与 DWD 精确对账（金额到分）----
    dwd_sources = {
        "gmv": ("SUM(amount)", "lakehouse.dwd_trade_order_detail"),
        "order_cnt": ("COUNT(*)", "lakehouse.dwd_trade_order_detail"),
        "payment_cnt": ("SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END)", "lakehouse.dwd_trade_payment_detail"),
        "payment_amount": ("SUM(CASE WHEN payment_status = 'SUCCESS' THEN amount ELSE 0 END)", "lakehouse.dwd_trade_payment_detail"),
        "payment_fail_cnt": ("SUM(CASE WHEN payment_status <> 'SUCCESS' THEN 1 ELSE 0 END)", "lakehouse.dwd_trade_payment_detail"),
        "refund_cnt": ("COUNT(*)", "lakehouse.dwd_trade_refund_detail"),
        "refund_amount": ("SUM(refund_amount)", "lakehouse.dwd_trade_refund_detail"),
    }
    for column, (expr, source) in dwd_sources.items():
        checker.check(
            f"1m {column} 合计 == DWD",
            scalar(spark, f"SELECT SUM({column}) FROM lakehouse.ads_batch_trade_1m"),
            scalar(spark, f"SELECT {expr} FROM {source}"),
        )

    # ---- 3. 天表 == 分钟表按天上卷（保证"天 = 分钟之和"）----
    day_pairs = (
        "gmv",
        "order_cnt",
        "payment_cnt",
        "payment_amount",
        "payment_fail_cnt",
        "refund_cnt",
        "refund_amount",
    )
    for column in day_pairs:
        checker.check(
            f"1d {column} == 1m 按天上卷",
            scalar(spark, f"SELECT SUM({column}) FROM lakehouse.ads_batch_trade_1d"),
            scalar(spark, f"SELECT SUM({column}) FROM lakehouse.ads_batch_trade_1m"),
        )

    # 天表的下单用户数必须来自 DWD 精确去重 —— 逐日核对，而不是把全表相加。
    #
    # !! 为什么不能 SUM(order_user_cnt) 与"全局去重用户数"比 !!
    #   去重计数不可加：同一用户可能在多天下单，
    #   逐日之和（5883）必然大于全局去重（1195）。
    #   拿两者相等做断言，是把"两个不同定义"当成"同一个数"——
    #   属于断言写错（上一版就是如此），必须逐日核对才有意义。
    day_user_mismatch = len(
        spark.sql(
            """
            SELECT d.dt
            FROM lakehouse.ads_batch_trade_1d d
            LEFT JOIN (
                SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
                       SIZE(COLLECT_SET(user_id))          AS true_users
                FROM lakehouse.dwd_trade_order_detail
                GROUP BY CAST(TO_DATE(event_time) AS STRING)
            ) t ON d.dt = t.dt
            WHERE d.order_user_cnt <> COALESCE(t.true_users, 0)
            """
        ).collect()
    )
    checker.check("1d 每日 order_user_cnt == DWD 当日精确去重", day_user_mismatch, 0)

    # 单调性健全性检查：全局去重下单用户数 >= 任意单日去重下单用户数。
    #   这条看着"显然"，但它能抓住一类真实错误：
    #   某天把 order_user_cnt 写成"分钟值相加"，会得到大于全局去重的数字。
    global_users = exact_distinct(spark, "user_id", "lakehouse.dwd_trade_order_detail")
    max_daily_users = int(
        scalar(spark, "SELECT COALESCE(MAX(order_user_cnt), 0) FROM lakehouse.ads_batch_trade_1d")
    )
    checker.check_true(
        "全局去重下单用户数 >= 单日最大值",
        global_users >= max_daily_users,
        f"全局 {global_users} vs 单日最大 {max_daily_users}",
    )

    # ---- 4. 类目维度自洽 ----
    checker.check(
        "类目 1m GMV 合计 == DWD",
        scalar(spark, "SELECT SUM(gmv) FROM lakehouse.ads_batch_category_1m"),
        scalar(spark, "SELECT SUM(amount) FROM lakehouse.dwd_trade_order_detail"),
    )
    checker.check(
        "类目 1d GMV 合计 == DWD",
        scalar(spark, "SELECT SUM(gmv) FROM lakehouse.ads_batch_category_1d"),
        scalar(spark, "SELECT SUM(amount) FROM lakehouse.dwd_trade_order_detail"),
    )
    checker.check(
        "类目 1d 行数 == 1m 的（日×类目）组合数",
        int(scalar(spark, "SELECT COUNT(*) FROM lakehouse.ads_batch_category_1d")),
        len(
            spark.sql(
                "SELECT DISTINCT dt, category_name FROM lakehouse.ads_batch_category_1m"
            ).collect()
        ),
    )

    # ---- 5. 与实时表"同形"检查（字段名 + 类型）----
    #
    # 这是批流对账的结构前提：字段错位会让差异看起来是"算法不同"，
    # 实际只是列顺序不一致。这里在写数据之后就立即校验，
    # 不要等到对账脚本里才发现。
    describe = {
        row["col_name"]: row["data_type"]
        for row in spark.sql("DESCRIBE lakehouse.ads_batch_trade_1m").collect()
        if row["col_name"] and not row["col_name"].startswith("#")
    }
    for name, expected_type in REALTIME_TRADE_COLUMNS:
        checker.check(
            f"1m 字段类型 {name}",
            describe.get(name, "<缺失>"),
            expected_type,
        )
    extra = sorted(set(describe) - {n for n, _ in REALTIME_TRADE_COLUMNS} - {"dt"})
    checker.check("1m 无多余字段（除分区列 dt）", extra, [])

    exit_code = checker.finish()
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
