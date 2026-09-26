"""Sprint 3：DWD → DWS（汇总层）构建作业。

运行方式（服务器上，仓库根目录）：
    bash scripts/submit-offline-job.sh --stage dws

它做什么：
    1. 确保 DWS 表存在（sql/hive/03_dws_tables.sql）；
    2. 执行 infrastructure/spark/sql/02_dws_build.sql：
       按天 / 按天×类目 / 按天×用户做轻度聚合，只出可加指标；
    3. **作业内自检**：DWS 的每日可加量必须与 DWD 直接聚合的结果逐项相等。

为什么断言要"逐项相等"而不是只看行数：
    行数对上但金额算错是完全可能的（例如三条流 LEFT JOIN 时把某天放大两倍）。
    金额类断言必须精确到分（DECIMAL 直接比较，不用容差）。
"""

from __future__ import annotations

import sys

from _common import Checker, build_spark, ensure_tables, run_sql_file


def scalar(spark, sql: str):
    return spark.sql(sql).first()[0]


def main() -> int:
    spark = build_spark("sprint3-build-dws")

    print("=" * 60, flush=True)
    print(" Sprint 3 — DWD → DWS 轻度聚合", flush=True)
    print("=" * 60, flush=True)

    ensure_tables(spark)
    run_sql_file(spark, "02_dws_build.sql")

    checker = Checker("DWS 层自检")

    # ---- 1. 日期骨架：DWS 交易总览的天数 == 三条流出现过的不同日期数 ----
    #
    # 用精确去重（_common.exact_distinct）：COUNT(DISTINCT dt) 是近似算法，
    # 拿它做"天数必须相等"的断言会随机失败（Sprint 3 踩坑）。
    # 注意日期骨架是"整数天"，与"下单用户数"这类大基数去重不同，
    # 这里的数据量很小，精确计算的代价可以忽略。
    expected_days = len(
        spark.sql(
            """
            SELECT DISTINCT dt FROM (
                SELECT CAST(TO_DATE(event_time) AS STRING) AS dt FROM lakehouse.dwd_trade_order_detail
                UNION
                SELECT CAST(TO_DATE(event_time) AS STRING) AS dt FROM lakehouse.dwd_trade_payment_detail
                UNION
                SELECT CAST(TO_DATE(event_time) AS STRING) AS dt FROM lakehouse.dwd_trade_refund_detail
            ) t
            """
        ).collect()
    )
    actual_days = int(scalar(spark, "SELECT COUNT(*) FROM lakehouse.dws_trade_overview_1d"))
    checker.check("dws_trade_overview_1d 天数", actual_days, expected_days)

    # ---- 2. 可加指标逐项对账（DWS vs DWD 直接聚合）----
    pairs = (
        ("gmv", "SUM(amount)", "lakehouse.dwd_trade_order_detail"),
        ("order_cnt", "COUNT(*)", "lakehouse.dwd_trade_order_detail"),
        ("total_quantity", "SUM(quantity)", "lakehouse.dwd_trade_order_detail"),
    )
    for column, expr, source in pairs:
        dws_value = scalar(spark, f"SELECT SUM({column}) FROM lakehouse.dws_trade_overview_1d")
        dwd_value = scalar(spark, f"SELECT {expr} FROM {source}")
        checker.check(f"交易总览 {column} 合计 == DWD", dws_value, dwd_value)

    payment_pairs = (
        ("payment_cnt", "SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END)"),
        ("payment_amount", "SUM(CASE WHEN payment_status = 'SUCCESS' THEN amount ELSE 0 END)"),
        ("payment_fail_cnt", "SUM(CASE WHEN payment_status <> 'SUCCESS' THEN 1 ELSE 0 END)"),
    )
    for column, expr in payment_pairs:
        dws_value = scalar(spark, f"SELECT SUM({column}) FROM lakehouse.dws_trade_overview_1d")
        dwd_value = scalar(spark, f"SELECT {expr} FROM lakehouse.dwd_trade_payment_detail")
        checker.check(f"交易总览 {column} 合计 == DWD", dws_value, dwd_value)

    refund_pairs = (
        ("refund_cnt", "COUNT(*)"),
        ("refund_amount", "SUM(refund_amount)"),
    )
    for column, expr in refund_pairs:
        dws_value = scalar(spark, f"SELECT SUM({column}) FROM lakehouse.dws_trade_overview_1d")
        dwd_value = scalar(spark, f"SELECT {expr} FROM lakehouse.dwd_trade_refund_detail")
        checker.check(f"交易总览 {column} 合计 == DWD", dws_value, dwd_value)

    # ---- 3. 类目维度：GMV 合计必须与订单明细一致 ----
    checker.check(
        "类目销售 GMV 合计 == 订单明细",
        scalar(spark, "SELECT SUM(gmv) FROM lakehouse.dws_trade_category_1d"),
        scalar(spark, "SELECT SUM(amount) FROM lakehouse.dwd_trade_order_detail"),
    )
    checker.check(
        "类目销售件数合计 == 订单明细",
        int(scalar(spark, "SELECT SUM(total_quantity) FROM lakehouse.dws_trade_category_1d")),
        int(scalar(spark, "SELECT SUM(quantity) FROM lakehouse.dwd_trade_order_detail")),
    )

    # ---- 4. 用户维度：GMV 合计与订单数合计必须一致 ----
    checker.check(
        "用户交易 GMV 合计 == 订单明细",
        scalar(spark, "SELECT SUM(gmv) FROM lakehouse.dws_trade_user_1d"),
        scalar(spark, "SELECT SUM(amount) FROM lakehouse.dwd_trade_order_detail"),
    )
    checker.check(
        "用户交易订单数合计 == 订单明细",
        int(scalar(spark, "SELECT SUM(order_cnt) FROM lakehouse.dws_trade_user_1d")),
        int(scalar(spark, "SELECT COUNT(*) FROM lakehouse.dwd_trade_order_detail")),
    )

    exit_code = checker.finish()
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
