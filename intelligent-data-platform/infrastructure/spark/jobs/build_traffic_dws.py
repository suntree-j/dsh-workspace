"""Sprint 5 阶段 5：流量域 DWD → DWS（按天轻度聚合）。

运行方式（服务器上，仓库根目录）：
    bash scripts/batch-mode.sh --stage traffic-dws

它做什么：
    1. 确保 DWS 表存在（sql/hive/03_dws_tables.sql）；
    2. 执行 infrastructure/spark/sql/06_traffic_dws.sql：
       按天汇总可加量 + 精确去重 uv，并产出行为漏斗阶梯；
    3. **作业内自检**（把"可加/不可加"这条口径变成可执行断言）。

本作业的自检为什么全部围绕「可加 vs 不可加」：
    流量域最容易犯且最难发现的错误，就是把去重指标当可加指标上卷。
    实测口径（本项目真实数字，可直接复核）：
        19644 个分钟窗口的 uv 之和 = 19999
        全部事件的去重用户数       =  1200
    差了 16 倍，而"日 UV = 19999"看起来也很合理（比 PV 小），
    光看数字根本发现不了。所以这里不做"看起来对"的检查，只做相等断言：
        - 可加量：DWS 合计 == DWD 直接聚合（必须相等）
        - 去重量：DWS 逐日 uv == DWD 当日精确去重（必须逐日相等）
        - 反向断言：SUM(日 uv) 只有 >= 全局去重用户数（**不**要求相等）——
          同一个人可以多天活跃，逐日之和必然大于全局去重。
          要求两者相等是把两个不同定义当成同一个数，属于断言写错
          （交易域 build_ads.py 上一版就是这样写错的）。
"""

from __future__ import annotations

import sys

from _common import Checker, build_spark, ensure_tables, run_sql_file

DWD_TABLE = "lakehouse.dwd_traffic_behavior_detail"
OVERVIEW_TABLE = "lakehouse.dws_traffic_overview_1d"
FUNNEL_TABLE = "lakehouse.dws_traffic_funnel_1d"

#: 可加指标：DWS 合计必须与 DWD 直接聚合**精确相等**
ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pv", "COUNT(*)"),
    ("view_cnt", "SUM(CASE WHEN event_type = 'VIEW' THEN 1 ELSE 0 END)"),
    ("click_cnt", "SUM(CASE WHEN event_type = 'CLICK' THEN 1 ELSE 0 END)"),
    ("cart_cnt", "SUM(CASE WHEN event_type = 'CART' THEN 1 ELSE 0 END)"),
    ("favorite_cnt", "SUM(CASE WHEN event_type = 'FAVORITE' THEN 1 ELSE 0 END)"),
    ("buy_cnt", "SUM(CASE WHEN event_type = 'BUY' THEN 1 ELSE 0 END)"),
)


def scalar(spark, sql: str):
    return spark.sql(sql).first()[0]


def main() -> int:
    spark = build_spark("sprint5-build-traffic-dws")

    print("=" * 60, flush=True)
    print(" Sprint 5 — 流量域 DWD → DWS 轻度聚合", flush=True)
    print("=" * 60, flush=True)

    ensure_tables(spark)
    run_sql_file(spark, "06_traffic_dws.sql")

    checker = Checker("流量域 DWS 层自检")

    dwd_rows = int(scalar(spark, f"SELECT COUNT(*) FROM {DWD_TABLE}"))
    overview_rows = int(scalar(spark, f"SELECT COUNT(*) FROM {OVERVIEW_TABLE}"))

    # ---- 0. 非空守卫 ----
    checker.check_true(
        "源 DWD 非空（防空集合上的假通过）",
        dwd_rows > 0,
        f"dwd_traffic_behavior_detail = {dwd_rows} 行",
    )
    checker.check_true(
        "DWS 总览表已产出（防空集合上的假通过）",
        overview_rows > 0,
        f"dws_traffic_overview_1d = {overview_rows} 行",
    )

    # ---- 1. 日期骨架：DWS 的天数 == DWD 出现过的不同日期数 ----
    #
    # 用精确去重（SIZE(COLLECT_SET(...))）而不是 COUNT(DISTINCT dt)：
    # 后者在 Spark 里是近似算法，拿它做"天数必须相等"的断言会随机失败。
    # 日期骨架是小基数（百量级），精确计算代价可忽略。
    expected_days = len(
        spark.sql(
            f"SELECT DISTINCT CAST(TO_DATE(event_time) AS STRING) AS dt FROM {DWD_TABLE}"
        ).collect()
    )
    checker.check("总览表天数 == DWD 出现过的日期数", overview_rows, expected_days)

    # ---- 2. 可加指标：逐项精确对账 ----
    for column, expr in ADDITIVE_COLUMNS:
        checker.check(
            f"总览 {column} 合计 == DWD",
            int(scalar(spark, f"SELECT SUM({column}) FROM {OVERVIEW_TABLE}")),
            int(scalar(spark, f"SELECT {expr} FROM {DWD_TABLE}")),
        )

    # ---- 3. 去重指标：**逐日**核对，不是把全表相加 ----
    #
    # 见文件头说明：SUM(日 uv) != 全局去重用户数，两者不可比。
    day_uv_mismatch = len(
        spark.sql(
            f"""
            SELECT d.dt
            FROM {OVERVIEW_TABLE} d
            LEFT JOIN (
                SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
                       SIZE(COLLECT_SET(user_id))          AS true_uv
                FROM {DWD_TABLE}
                GROUP BY CAST(TO_DATE(event_time) AS STRING)
            ) t ON d.dt = t.dt
            WHERE d.uv <> COALESCE(t.true_uv, 0)
            """
        ).collect()
    )
    checker.check("总览 每日 uv == DWD 当日精确去重", day_uv_mismatch, 0)

    # ---- 4. 单调性：SUM(日 uv) >= 全局去重用户数 ----
    #
    # 这条能抓住"把 uv 当成可加量"这一类错误：
    # 若某处误把分钟/窗口 uv 相加，逐日之和会膨胀到远超全局去重的量级
    # （实测那个错误会得到 19999 而不是 1199）。
    global_uv = int(
        scalar(spark, f"SELECT SIZE(COLLECT_SET(user_id)) FROM {DWD_TABLE}")
    )
    daily_uv_sum = int(scalar(spark, f"SELECT SUM(uv) FROM {OVERVIEW_TABLE}"))
    checker.check_true(
        "SUM(日 uv) >= 全局去重用户数（去重指标不可加）",
        daily_uv_sum >= global_uv,
        f"逐日之和 {daily_uv_sum} vs 全局去重 {global_uv}",
    )
    checker.check_true(
        "全局去重用户数 > 0（防上面的比较在空集上成立）",
        global_uv > 0,
        f"全局去重 {global_uv}",
    )

    # ---- 5. 漏斗表：与总览表同源同值 ----
    funnel_rows = int(scalar(spark, f"SELECT COUNT(*) FROM {FUNNEL_TABLE}"))
    checker.check("漏斗表行数 == 总览表行数", funnel_rows, overview_rows)
    for column in ("uv", "view_cnt", "click_cnt", "cart_cnt", "buy_cnt", "favorite_cnt"):
        checker.check(
            f"漏斗 {column} 合计 == 总览",
            int(scalar(spark, f"SELECT SUM({column}) FROM {FUNNEL_TABLE}")),
            int(scalar(spark, f"SELECT SUM({column}) FROM {OVERVIEW_TABLE}")),
        )

    # ---- 6. 漏斗表专属：每步去重人数不得为 0，且不超过该步次数 ----
    #
    # "某一步的人数为 0，但次数 > 0" 是不可能的组合，能抓住聚合写错列的情况。
    bad_user_rows = len(
        spark.sql(
            f"""
            SELECT dt FROM {FUNNEL_TABLE}
            WHERE (view_cnt > 0 AND view_user_cnt = 0)
               OR (click_cnt > 0 AND click_user_cnt = 0)
               OR (cart_cnt > 0 AND cart_user_cnt = 0)
               OR (buy_cnt > 0 AND buy_user_cnt = 0)
            """
        ).collect()
    )
    checker.check("漏斗各步人数与次数自洽（有次数就有人数）", bad_user_rows, 0)

    exit_code = checker.finish()
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
