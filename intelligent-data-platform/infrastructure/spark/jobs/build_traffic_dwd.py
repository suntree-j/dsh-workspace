"""Sprint 5 阶段 5：流量域 ODS → DWD（行为事件明细）。

运行方式（服务器上，仓库根目录）：
    bash scripts/batch-mode.sh --stage traffic-dwd
    # 或 bash scripts/run-batch-pipeline.sh --stage traffic-dwd

它做什么：
    1. 确保 DWD 表存在（sql/hive/02_dwd_tables.sql）；
    2. 执行 infrastructure/spark/sql/05_traffic_dwd.sql：
       去重(event_id) → 清洗(枚举/空值) → 维度补全(category_name) → 统一命名(dt)；
    3. **作业内自检**（7 项）：
       - 行数 == ODS 行数（白名单过滤 + 去重当前都不应丢行）；
       - event_id 主键唯一；
       - event_time / user_id 无空值；
       - event_type 全在 5 类白名单内；
       - 5 类事件的计数与 ODS 逐类相等（漏斗基线，Sprint 4 已记录）；
       - 表非空守卫（防空表上"全绿通过"）。

为什么"行数必须等于 ODS"是硬断言（与交易域 DWD 同一条纪律）：
    实测基线（2024-10-02 ~ 2026-09-26 的归档数据）：
        ODS 20000 行、event_id 去重后 20000 个（无重复）、
        事件类型只有 5 类（没有 SEARCH）、product_id 在商品维表 100% 命中。
    因此当前不该丢任何一行。这条断言挂了只有三种可能：
      a) 归档侧产生了脏数据（去重键冲突 / 出现新枚举）；
      b) 清洗条件写错了；
      c) 维表补全把行放大了（join 键重复）。
    三种都必须先查清楚，绝不能把断言放宽了事（AGENTS.md 第 8.5 节）。

为什么维表补全用 LEFT JOIN 而不是 INNER JOIN：
    INNER JOIN 会在商品维表缺该 product_id 时**静默丢行**，
    而对账报出来的现象是"离线少算"，根因却在一次 join 上。
    LEFT JOIN 把缺失暴露为 category_name = NULL，行数不受影响 ——
    而 category_name 本就不参与对账（实时侧那一列实测为空，见 05_traffic_dwd.sql）。
"""

from __future__ import annotations

import sys

from _common import Checker, build_spark, describe_columns, ensure_tables, run_sql_file

DWD_TABLE = "lakehouse.dwd_traffic_behavior_detail"
ODS_TABLE = "lakehouse.ods_behavior_event"

#: 进入漏斗的事件类型（与实时侧 dwd_traffic_behavior_detail 落库的枚举一致）
EVENT_TYPES: tuple[str, ...] = ("VIEW", "CLICK", "CART", "FAVORITE", "BUY")

#: 与 sql/doris/10_dwd_tables.sql 的 dwd_traffic_behavior_detail 必须逐列同形的
#: **共有列**（实时侧另有 dt DATE 列，离线侧 dt 是分区列，故不在此列）。
REALTIME_DWD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("event_id", "string"),
    ("event_type", "string"),
    ("user_id", "bigint"),
    ("product_id", "bigint"),
    ("category_name", "string"),
    ("device", "string"),
    ("province", "string"),
    ("event_time", "timestamp"),
)


def scalar(spark, sql: str):
    return spark.sql(sql).first()[0]


def main() -> int:
    spark = build_spark("sprint5-build-traffic-dwd")

    print("=" * 60, flush=True)
    print(" Sprint 5 — 流量域 ODS → DWD 构建", flush=True)
    print("=" * 60, flush=True)

    ensure_tables(spark)
    run_sql_file(spark, "05_traffic_dwd.sql")

    checker = Checker("流量域 DWD 层自检")

    # ---- 0. 非空守卫：空表上的相等断言是空洞的（Sprint 4 的教训） ----
    ods_rows = int(scalar(spark, f"SELECT COUNT(*) FROM {ODS_TABLE}"))
    dwd_rows = int(scalar(spark, f"SELECT COUNT(*) FROM {DWD_TABLE}"))
    checker.check_true(
        "源 ODS 非空（防空集合上的假通过）",
        ods_rows > 0,
        f"ods_behavior_event = {ods_rows} 行",
    )

    # ---- 1. 行数与 ODS 一致 ----
    checker.check("dwd 行数 == ods 行数", dwd_rows, ods_rows)

    # ---- 2. 主键唯一：去重是否真的生效 ----
    duplicates = int(
        scalar(
            spark,
            f"""
            SELECT COUNT(*) AS c FROM (
                SELECT event_id FROM {DWD_TABLE}
                GROUP BY event_id HAVING COUNT(*) > 1
            ) t
            """,
        )
    )
    checker.check("event_id 主键唯一（去重生效）", duplicates, 0)

    # ---- 3. 关键字段无空值 ----
    for column in ("event_time", "user_id", "event_id"):
        nulls = int(scalar(spark, f"SELECT COUNT(*) FROM {DWD_TABLE} WHERE {column} IS NULL"))
        checker.check(f"{column} 无空值", nulls, 0)

    # ---- 4. 事件类型枚举合法 ----
    bad_types = [
        row["event_type"]
        for row in spark.sql(
            f"SELECT DISTINCT event_type FROM {DWD_TABLE} "
            f"WHERE event_type NOT IN ({', '.join(repr(t) for t in EVENT_TYPES)})"
        ).collect()
    ]
    checker.check("event_type 全在 5 类白名单内", bad_types, [])

    # ---- 5. 漏斗基线：逐类计数与 ODS 相等 ----
    #
    # 白名单过滤当前不应丢任何行，所以这里必须是**逐类相等**而不是"小于等于"。
    # 若将来归档开始产生 SEARCH 事件，这条会失败 —— 那正是我们想要的信号：
    # 它意味着实时侧与离线侧的枚举范围出现了分叉，需要先对齐口径。
    ods_counts = {
        row["event_type"]: int(row["c"])
        for row in spark.sql(
            f"SELECT event_type, COUNT(*) AS c FROM {ODS_TABLE} GROUP BY event_type"
        ).collect()
    }
    dwd_counts = {
        row["event_type"]: int(row["c"])
        for row in spark.sql(
            f"SELECT event_type, COUNT(*) AS c FROM {DWD_TABLE} GROUP BY event_type"
        ).collect()
    }
    for event_type in EVENT_TYPES:
        checker.check(
            f"{event_type} 计数 == ODS",
            dwd_counts.get(event_type, 0),
            ods_counts.get(event_type, 0),
        )

    # ---- 6. 漏斗逐级收窄（AGENTS.md 8.2 的数据正确性断言） ----
    checker.check_true(
        "漏斗逐级收窄 VIEW>CLICK>CART>BUY",
        dwd_counts.get("VIEW", 0) > dwd_counts.get("CLICK", 0)
        > dwd_counts.get("CART", 0) > dwd_counts.get("BUY", 0),
        f"VIEW={dwd_counts.get('VIEW', 0)} CLICK={dwd_counts.get('CLICK', 0)} "
        f"CART={dwd_counts.get('CART', 0)} BUY={dwd_counts.get('BUY', 0)}",
    )

    # ---- 7. 与实时 DWD 同形（字段名 + 类型） ----
    #
    # 两边同名同类型，下游 DWS/ADS 才能用同一套公式复算。
    # 用 describe_columns 而不是手写 DESCRIBE 过滤：
    # Spark 的 DESCRIBE 会给列名右填充空格，直接比较会静默取不到值。
    describe = describe_columns(spark, DWD_TABLE)
    for name, expected_type in REALTIME_DWD_COLUMNS:
        checker.check(f"字段类型 {name}", describe.get(name, "<缺失>"), expected_type)

    exit_code = checker.finish()
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
