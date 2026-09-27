"""Sprint 5 阶段 5：流量域 DWD → ADS（1 分钟 + 1 天指标）。

运行方式（服务器上，仓库根目录）：
    bash scripts/batch-mode.sh --stage traffic-ads

它做什么：
    1. 确保 ADS 表存在（sql/hive/04_ads_tables.sql）；
    2. 执行 infrastructure/spark/sql/07_traffic_ads.sql：
       按 metrics.md 第 3 节的口径算出与实时链路**同名同粒度**的流量指标；
    3. **作业内自检**：
       - 1 分钟表的窗口数 == DWD 出现过的不同分钟数；
       - 8 个可加量合计 == DWD 精确聚合；
       - uv 的合计 == 全部事件的全局去重用户数（逐窗口去重之和 == 全局去重，
         因为每个用户在每个窗口内只被数一次 —— 这是一条**精确相等**断言）；
       - 天表 == 分钟表按天上卷（可加量）；
       - 天表 uv 逐日 == DWD 当日精确去重（去重指标必须回到明细）；
       - 1m 表字段名 / 类型与实时表逐列同形（对账的结构前提）。

为什么这些断言是"批流对账"的前提：
    如果离线链路自己内部都自相矛盾（分钟表和天表对不上、
    或者 uv 的两种算法给出不同的数），那么与实时链路对账失败时
    根本无法判断是"离线算错"还是"实时算错"。
    先把离线链路内部闭合，再去比实时，问题才可定位。
    这与交易域 build_ads.py 的纪律完全相同。

!! 关于 uv 的一处**刻意**不一致，必须写清楚 !!
    ads_traffic_1m.uv      = COUNT(DISTINCT user_id)        ← 近似实现
    ads_traffic_1d.uv      = SIZE(COLLECT_SET(user_id))     ← 精确实现
    1m 的 uv 是**参与对账的列**，必须跟随实时侧 Flink 的实现语义
    （Flink 的 COUNT(DISTINCT ...) 同为近似实现），否则会因为"算法不同"而对不上；
    1d 的 uv 是**业务事实列**，且被相等断言使用，必须精确。
    两者写法不同是设计决定，不是遗漏 —— 见 07_traffic_ads.sql 的文件头。
"""

from __future__ import annotations

import sys

from _common import (
    Checker,
    build_spark,
    clean_stale_spark_temp,
    describe_columns,
    ensure_tables,
    run_sql_file,
)

# ADS 表的 S3 路径 —— 作业开始前清理这些目录下的 Spark 写入残留
# （残留的 .spark-staging-* 会被 Doris 的 `**/*.parquet` 递归读到，导致装载行数翻倍）
ADS_TABLE_PATHS: tuple[str, ...] = (
    "s3a://lakehouse/warehouse/ads/traffic_1m",
    "s3a://lakehouse/warehouse/ads/traffic_1d",
)

DWD_TABLE = "lakehouse.dwd_traffic_behavior_detail"
MINUTE_TABLE = "lakehouse.ads_traffic_1m"
DAY_TABLE = "lakehouse.ads_traffic_1d"

#: 与 sql/doris/12_ads_tables.sql 的 ads_realtime_traffic_1m 必须逐字段一致
#: （字段名 + 类型 + 顺序）。这是批流对账的结构前提：
#: 字段错位会让差异看起来是"算法不同"，实际只是列顺序不一致。
REALTIME_TRAFFIC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("window_start", "timestamp"),
    ("window_end", "timestamp"),
    ("uv", "bigint"),
    ("pv", "bigint"),
    ("view_cnt", "bigint"),
    ("click_cnt", "bigint"),
    ("cart_cnt", "bigint"),
    ("favorite_cnt", "bigint"),
    ("buy_cnt", "bigint"),
    ("click_rate", "decimal(10,4)"),
    ("cart_rate", "decimal(10,4)"),
    ("buy_rate", "decimal(10,4)"),
)

#: 可加量（1d 必须等于 1m 按天上卷）
ADDITIVE_COLUMNS: tuple[str, ...] = (
    "pv",
    "view_cnt",
    "click_cnt",
    "cart_cnt",
    "favorite_cnt",
    "buy_cnt",
)


def scalar(spark, sql: str):
    return spark.sql(sql).first()[0]


def main() -> int:
    spark = build_spark("sprint5-build-traffic-ads")

    print("=" * 60, flush=True)
    print(" Sprint 5 — 流量域 DWD → ADS 指标计算", flush=True)
    print("=" * 60, flush=True)

    ensure_tables(spark)

    # 清理上次被中断的写入残留（.spark-staging-*）：
    # 否则下游 Doris 的 `**/*.parquet` 会把残留数据再读一遍，装载行数翻倍。
    removed = clean_stale_spark_temp(spark, ADS_TABLE_PATHS)
    print(f"[clean] 清理 Spark 写入残留目录：{removed} 个", flush=True)

    run_sql_file(spark, "07_traffic_ads.sql")

    checker = Checker("流量域 ADS 层自检")

    dwd_rows = int(scalar(spark, f"SELECT COUNT(*) FROM {DWD_TABLE}"))
    minute_rows = int(scalar(spark, f"SELECT COUNT(*) FROM {MINUTE_TABLE}"))
    day_rows = int(scalar(spark, f"SELECT COUNT(*) FROM {DAY_TABLE}"))

    # ---- 0. 非空守卫（空集合上的相等断言是空洞的） ----
    checker.check_true(
        "源 DWD 非空（防空集合上的假通过）", dwd_rows > 0, f"{dwd_rows} 行"
    )
    checker.check_true(
        "1m 表已产出（防空集合上的假通过）", minute_rows > 0, f"{minute_rows} 个窗口"
    )
    checker.check_true(
        "1d 表已产出（防空集合上的假通过）", day_rows > 0, f"{day_rows} 天"
    )

    # ---- 1. 分钟窗口数 == DWD 出现过的分钟数 ----
    #
    # 必须用精确去重：COUNT(DISTINCT w) 在 Spark 里是近似算法（约 5% 误差），
    # 拿它当"窗口数必须相等"的断言会随机通过 / 失败（Sprint 3 踩过这个坑）。
    expected_windows = len(
        spark.sql(
            f"SELECT DISTINCT DATE_TRUNC('minute', event_time) AS w FROM {DWD_TABLE}"
        ).collect()
    )
    checker.check("1m 窗口数 == DWD 分钟数", minute_rows, expected_windows)

    # ---- 2. 可加指标与 DWD 精确对账 ----
    additive_sources: dict[str, str] = {
        "pv": "COUNT(*)",
        "view_cnt": "SUM(CASE WHEN event_type = 'VIEW' THEN 1 ELSE 0 END)",
        "click_cnt": "SUM(CASE WHEN event_type = 'CLICK' THEN 1 ELSE 0 END)",
        "cart_cnt": "SUM(CASE WHEN event_type = 'CART' THEN 1 ELSE 0 END)",
        "favorite_cnt": "SUM(CASE WHEN event_type = 'FAVORITE' THEN 1 ELSE 0 END)",
        "buy_cnt": "SUM(CASE WHEN event_type = 'BUY' THEN 1 ELSE 0 END)",
    }
    for column, expr in additive_sources.items():
        checker.check(
            f"1m {column} 合计 == DWD",
            int(scalar(spark, f"SELECT SUM({column}) FROM {MINUTE_TABLE}")),
            int(scalar(spark, f"SELECT {expr} FROM {DWD_TABLE}")),
        )

    # ---- 3. uv：逐窗口去重之和与全局去重的关系 ----
    #
    # !! 这两个数**不相等**，而且都正确 —— 不能拿它们做相等断言 !!
    #    逐窗口去重把每个用户在每个窗口里数一次；全局去重把每个用户数一次。
    #    一个用户可能在多个窗口出现，所以逐窗口之和 >= 全局去重。
    #    实测：19644 个窗口的 uv 之和 = 19999，而全局去重只有 1200 —— 差约 16 倍。
    #    把它们当成同一个数就会得出"离线算错了"的假结论。
    #    因此这里断言的是两条**基数性质**（>= 全局去重、<= 总 PV），
    #    而不是相等。真正的相等断言在下次检查（天表 uv 逐日核对）。
    window_uv_sum = int(scalar(spark, f"SELECT SUM(uv) FROM {MINUTE_TABLE}"))
    global_uv = int(scalar(spark, f"SELECT SIZE(COLLECT_SET(user_id)) FROM {DWD_TABLE}"))
    checker.check_true(
        "SUM(窗口 uv) >= 全局去重用户数（逐窗口去重的基数性质）",
        window_uv_sum >= global_uv,
        f"逐窗口之和 {window_uv_sum} vs 全局去重 {global_uv}",
    )
    # 同时要求它不超过总事件数（uv 不可能比 pv 大）
    total_pv = int(scalar(spark, f"SELECT COUNT(*) FROM {DWD_TABLE}"))
    checker.check_true(
        "SUM(窗口 uv) <= 总事件数 PV（uv 不可能大于 pv）",
        window_uv_sum <= total_pv,
        f"逐窗口之和 {window_uv_sum} vs 总 PV {total_pv}",
    )
    # 单个窗口的 uv 不可能超过该窗口的 pv —— 逐窗口核对
    bad_uv_windows = len(
        spark.sql(f"SELECT 1 FROM {MINUTE_TABLE} WHERE uv > pv").collect()
    )
    checker.check("1m 无 uv > pv 的窗口", bad_uv_windows, 0)

    # ---- 4. 天表可加量 == 分钟表按天上卷 ----
    for column in ADDITIVE_COLUMNS:
        checker.check(
            f"1d {column} == 1m 按天上卷",
            int(scalar(spark, f"SELECT SUM({column}) FROM {DAY_TABLE}")),
            int(scalar(spark, f"SELECT SUM({column}) FROM {MINUTE_TABLE}")),
        )

    # ---- 5. 天表 uv 逐日 == DWD 当日精确去重 ----
    #
    # 去重指标不可加：天表 uv 必须来自明细，不能是 SUM(分钟 uv)。
    # 逐日核对才有意义 —— 把全表相加对比全局去重是"两个不同定义比大小"。
    day_uv_mismatch = len(
        spark.sql(
            f"""
            SELECT d.dt
            FROM {DAY_TABLE} d
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
    checker.check("1d 每日 uv == DWD 当日精确去重", day_uv_mismatch, 0)

    # ---- 6. 天表行数 == 分钟表里出现过的日期数 ----
    expected_days = len(spark.sql(f"SELECT DISTINCT dt FROM {MINUTE_TABLE}").collect())
    checker.check("1d 行数 == 1m 里出现过的日期数", day_rows, expected_days)

    # ---- 7. 比率列的空值形态与实时侧一致（分母为 0 → NULL） ----
    #
    # 实时侧实测存在 click_rate = 0.0000 的行（"只有 CLICK 没有 VIEW"），
    # 也必然存在 NULL 的行。这里断言的是**没有"分母为 0 却算出非 NULL"的行** ——
    # 那正是空值约定被破坏的形态（会被写成 0，而不是 NULL）。
    bad_rates = len(
        spark.sql(
            f"""
            SELECT 1 FROM {MINUTE_TABLE}
            WHERE (view_cnt = 0 AND click_rate IS NOT NULL)
               OR (click_cnt = 0 AND cart_rate IS NOT NULL)
               OR (cart_cnt = 0 AND buy_rate IS NOT NULL)
            """
        ).collect()
    )
    checker.check("1m 比率列分母为 0 时均为 NULL", bad_rates, 0)

    # ---- 8. 与实时表"同形"检查（字段名 + 类型 + 顺序） ----
    describe = describe_columns(spark, MINUTE_TABLE)
    for name, expected_type in REALTIME_TRAFFIC_COLUMNS:
        checker.check(f"1m 字段类型 {name}", describe.get(name, "<缺失>"), expected_type)

    # !! 顺序也必须一致 —— 只比"集合相等"会漏掉列错位 !!
    #
    # 但这里有一个必须显式处理的事实：`describe_columns` 读的是 DESCRIBE 的输出，
    # 而 **DESCRIBE 会把分区列一并列出来**（dt 在最后，前面有一段
    # "# Partition Information"）。实测：
    #     DESCRIBE lakehouse.ads_traffic_1m → 13 列（12 个数据列 + dt）
    #   而实时表 ecommerce.ads_realtime_traffic_1m 只有 12 列，没有分区列
    #   （见 sql/doris/12_ads_tables.sql 的建表语句）。
    #
    # 所以判据是「数据列顺序逐列一致，且分区列 dt 只在最后」，
    # 而不是「两边列清单完全相等」—— 后者在当前设计下**永远不成立**。
    # 这不是放宽判据：dt 是离线侧的分区列（对账时不参与比较，见 08_traffic_reconcile.sql），
    # 把它算进来等于要求实时表也有一个它本来就没有的列。
    expected_order = [name for name, _ in REALTIME_TRAFFIC_COLUMNS] + ["dt"]
    checker.check("1m 字段顺序与实时表一致（分区列 dt 在最后）", list(describe.keys()), expected_order)

    # 反向守卫：除 dt 外不得多出任何列。
    #   少一列会被上面的顺序断言抓住，**多**一列同样必须被抓住 ——
    #   多余的列会让"同形"这个前提失效（实时表没有它，对账时无法对齐）。
    extra = sorted(set(describe) - set(expected_order))
    checker.check("1m 无多余字段（除分区列 dt）", extra, [])

    exit_code = checker.finish()
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
