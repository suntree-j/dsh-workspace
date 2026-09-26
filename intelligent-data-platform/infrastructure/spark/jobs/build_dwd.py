"""Sprint 3：ODS → DWD（明细层）构建作业。

运行方式（服务器上，仓库根目录）：
    bash scripts/submit-offline-job.sh --stage dwd     # 或用 run-batch-pipeline.sh 一次跑完

它做什么：
    1. 确保 DWD 表存在（sql/hive/02_dwd_tables.sql）；
    2. 执行 infrastructure/spark/sql/01_dwd_build.sql：
       去重 → 清洗 → 维度补全 → 统一命名（event_time / dt）；
    3. **作业内自检**：DWD 行数必须与 ODS 一致（清洗规则当前不应过滤掉任何行），
       且主键唯一、金额类型为 decimal(18,2)。

为什么"清洗后行数必须等于 ODS 行数"是硬断言：
    当前数据生成器的输出是经过业务校验的（见 tests/test_data_generator.py），
    因此 DWD 不应该丢掉任何一行。如果这条断言挂了，只有两种可能：
      a) 数据生成器回归了（产生了脏数据）；
      b) DWD 的清洗条件写错了。
    两种都必须先查清楚，绝不能把断言放宽了事（AGENTS.md 第 8.5 节）。
"""

from __future__ import annotations

import sys

from _common import Checker, build_spark, describe_columns, ensure_tables, run_sql_file

# DWD 表 → 对应的 ODS 表（用于行数一致性断言）
LAYER_PAIRS: tuple[tuple[str, str], ...] = (
    ("dwd_user_detail", "ods_user"),
    ("dwd_product_detail", "ods_product"),
    ("dwd_trade_order_detail", "ods_orders"),
    ("dwd_trade_payment_detail", "ods_payment"),
    ("dwd_trade_refund_detail", "ods_refund"),
)


def count(spark, table: str) -> int:
    return int(spark.sql(f"SELECT COUNT(*) AS c FROM lakehouse.{table}").first()["c"])


def main() -> int:
    spark = build_spark("sprint3-build-dwd")

    print("=" * 60, flush=True)
    print(" Sprint 3 — ODS → DWD 构建", flush=True)
    print("=" * 60, flush=True)

    ensure_tables(spark)
    run_sql_file(spark, "01_dwd_build.sql")

    checker = Checker("DWD 层自检")
    for dwd, ods in LAYER_PAIRS:
        checker.check(f"{dwd} 行数 == {ods}", count(spark, dwd), count(spark, ods))

    # 主键唯一性：去重是否真的生效
    duplicates = spark.sql(
        """
        SELECT COUNT(*) AS c FROM (
            SELECT order_id FROM lakehouse.dwd_trade_order_detail
            GROUP BY order_id HAVING COUNT(*) > 1
        ) t
        """
    ).first()["c"]
    checker.check("dwd_trade_order_detail 主键唯一", int(duplicates), 0)

    # 金额类型：必须是 decimal(18,2)，出现 double/float 会破坏"精确到分"的对账
    #
    # 用 describe_columns 而不是手写 DESCRIBE 过滤：Spark 的 DESCRIBE 会给列名
    # 右填充空格（详见 _common.describe_columns 的说明），直接比较会静默取不到值。
    order_columns = describe_columns(spark, "lakehouse.dwd_trade_order_detail")
    checker.check(
        "dwd_trade_order_detail.amount 类型",
        order_columns.get("amount", "<缺失>"),
        "decimal(18,2)",
    )

    # 事件时间不能为空（为空会让窗口聚合静默丢行）
    for table in (
        "dwd_trade_order_detail",
        "dwd_trade_payment_detail",
        "dwd_trade_refund_detail",
    ):
        null_events = spark.sql(
            f"SELECT COUNT(*) AS c FROM lakehouse.{table} WHERE event_time IS NULL"
        ).first()["c"]
        checker.check(f"{table}.event_time 无空值", int(null_events), 0)

    exit_code = checker.finish()
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
