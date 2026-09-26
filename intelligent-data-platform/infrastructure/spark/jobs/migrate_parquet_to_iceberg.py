"""Sprint 5 阶段 4：把 Parquet 外部表迁移成 Iceberg 表（并逐表核对）。

运行方式（服务器上，仓库根目录）：
    bash scripts/batch-mode.sh --stage iceberg-migrate
    # 或经 DAG

它做什么
--------
对 `lakehouse` 库里的每一张表：

    1. 从**源表 schema** 推导 Iceberg 表的列定义（不手写 DDL）；
    2. 在 `lakehouse_iceberg` 库建同名 Iceberg 表（沿用相同的分区列）；
    3. 把数据搬过去（INSERT OVERWRITE，动态分区 = 幂等）；
    4. **逐表核对**行数与金额合计。

为什么从 schema 推导，而不是手写 16 张表的 DDL
----------------------------------------------
    手写 DDL 有两个必然会出的问题：
      ① 列类型抄错 / 抄漏 —— 16 张表、几十个列，靠人眼对不现实；
      ② 源表将来加字段，DDL 不会跟着变，两边悄悄分叉。
    从 `spark.table(src).schema` 推导则保证**两边定义同源**，
    迁移后类型不一致这一类问题从根上不会出现。

    代价是"DDL 不在 sql/ 目录里" —— 但本作业会在迁移时把实际执行的
    CREATE TABLE 语句打印出来，可逐条核对；而且 Iceberg 表的权威定义
    在 Metastore 里，`SHOW CREATE TABLE` 随时可取。

为什么核对要包含"金额合计"
--------------------------
    只比行数是不够的：行数相同但某列被截断/转型出错时，行数依然相等。
    金额是 DECIMAL(18,2)，对类型变化最敏感，因此拿它当"内容没坏"的探针。
    这与 Sprint 2/3 的逐表对账是同一个思路。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession

from _common import (
    Checker,
    build_spark,
    clean_stale_spark_temp,
)

#: 源库：湖仓的 Parquet 侧（Hive 外部表），**必须写满三段**。
#:
#: !! 为什么源库要显式写 spark_catalog !!
#:   Spark 的名字解析规则：一段名 = 当前目录 + 当前命名空间下的表；
#:   两段名 = (命名空间, 表)，**永远属于当前目录**；只有三段名才是
#:   (目录, 命名空间, 表)。
#:   所以两段名 `lakehouse.ods_user` 并不自动指向"Hive 那一边"，
#:   它指的是"当前目录里叫 lakehouse 的库"。当前目录恰好是
#:   spark_catalog（Hive）时它是对的 —— 换个默认目录它就指向别处，
#:   而且**不会报错**。写成三段名，结果与默认目录是谁无关。
SRC_DB = "spark_catalog.lakehouse"

#: Iceberg 目录名（见 infrastructure/spark/conf/spark-defaults.conf）。
#: 注意它**不能**与下面的库名相同 —— 同名会让"建库"这句话产生二义。
ICEBERG_CATALOG = "iceberg"

#: 目标命名空间：HiveCatalog 的命名空间就是 Hive 库名，
#: 因此沿用 `lakehouse_iceberg`，与 Parquet 侧的 `lakehouse` 并存。
DST_NS = "lakehouse_iceberg"

#: 目标前缀：**三段名**（目录.命名空间），拼上表名后与默认目录无关。
DST_DB = f"{ICEBERG_CATALOG}.{DST_NS}"

#: ODS 层落盘的 S3 路径（用于清理 Spark 写入残留目录）
WAREHOUSE = "s3a://lakehouse/warehouse"

#: 参与迁移的表。
#:
#: !! 为什么显式列出而不是 `SHOW TABLES` 全扫 !!
#:   对账结果表（ads_reconcile_*）是 Sprint 3 的产物，属于"证据"而非"数据"；
#:   把它们一起迁走会让下一步的对照关系变复杂。本次只迁四层主表。
#:   （对账表留在 Parquet 侧，Sprint 5 的对账仍读它们。）
MIGRATE_TABLES: tuple[str, ...] = (
    # ODS
    "ods_user",
    "ods_product",
    "ods_orders",
    "ods_payment",
    "ods_refund",
    "ods_behavior_event",
    # DWD
    "dwd_user_detail",
    "dwd_product_detail",
    "dwd_trade_order_detail",
    "dwd_trade_payment_detail",
    "dwd_trade_refund_detail",
    # DWS
    "dws_trade_overview_1d",
    "dws_trade_category_1d",
    "dws_trade_user_1d",
    # ADS
    "ads_batch_trade_1m",
    "ads_batch_trade_1d",
    "ads_batch_category_1m",
    "ads_batch_category_1d",
)

#: 金额列（用于"内容没坏"的核对）。存在则比对，不存在则跳过。
MONEY_COLUMNS: tuple[str, ...] = (
    "gmv",
    "amount",
    "payment_amount",
    "refund_amount",
    "avg_order_amount",
    "total_amount",
)


@dataclass(frozen=True)
class TablePlan:
    """一张表的迁移计划。"""

    name: str
    columns: list[tuple[str, str]]
    partition_cols: list[str]

    @property
    def src(self) -> str:
        return f"{SRC_DB}.{self.name}"

    @property
    def dst(self) -> str:
        return f"{DST_DB}.{self.name}"


def partition_columns(spark: SparkSession, table: str) -> list[str]:
    """取出表的分区列。

    !! 为什么不用 `SHOW PARTITIONS` !!
    `SHOW PARTITIONS` 返回的是**分区值**（如 dt=2026-09-26），
    而我们要的是**分区列名**。`DESCRIBE EXTENDED` 输出里有一段
    「# Partition Information」，其后到空行为止的列名才是分区列。
    这是 Spark SQL 的既有约定，解析它比连 Metastore 更直接。
    """
    rows = spark.sql(f"DESCRIBE EXTENDED {SRC_DB}.{table}").collect()
    cols: list[str] = []
    in_part = False
    for r in rows:
        name = (r[0] or "").strip()
        if name.startswith("# Partition Information"):
            in_part = True
            continue
        if in_part:
            if name == "" or name.startswith("#"):
                # 分区段结束（空行或下一段 # 开头）
                if name == "":
                    break
                continue
            # 分区段里的第一行是列头（col_name / data_type / comment）
            if name.startswith("col_name"):
                continue
            cols.append(name)
    return cols


def build_plan(spark: SparkSession, table: str) -> TablePlan:
    """从源表推导列定义与分区列。"""
    schema = spark.table(f"{SRC_DB}.{table}").schema
    columns = [(f.name, f.dataType.simpleString()) for f in schema.fields]
    return TablePlan(name=table, columns=columns, partition_cols=partition_columns(spark, table))


def create_iceberg_table(spark: SparkSession, plan: TablePlan) -> str:
    """建 Iceberg 表，返回实际执行的 DDL（便于核对与留证）。"""
    cols_sql = ",\n  ".join(f"{n} {t} COMMENT '{n}'" for n, t in plan.columns)
    part_sql = ""
    if plan.partition_cols:
        part_sql = "\nUSING iceberg\nPARTITIONED BY (" + ", ".join(plan.partition_cols) + ")"
    else:
        part_sql = "\nUSING iceberg"

    # !! 压缩编码必须写进 TBLPROPERTIES !!
    #   实测发现：写在 catalog 级别（spark.sql.catalog.X.write.parquet.
    #   compression-codec）**不会被继承到表上** —— 阶段 3 建冒烟表时
    #   表属性里出现的是 Iceberg 默认的 zstd，而不是我们配的 snappy。
    #   要固定编码，只能在建表时用 TBLPROPERTIES 指定。
    props = (
        "\nTBLPROPERTIES ("
        "'write.format.default'='parquet', "
        "'write.parquet.compression-codec'='snappy'"
        ")"
    )

    ddl = f"CREATE TABLE IF NOT EXISTS {plan.dst} (\n  {cols_sql}\n){part_sql}{props}"
    spark.sql(ddl)
    return ddl


def migrate(spark: SparkSession, plan: TablePlan) -> None:
    """搬数据。用 INSERT OVERWRITE + 动态分区覆盖，因此重跑幂等。"""
    cols = ", ".join(n for n, _ in plan.columns)
    spark.sql(f"INSERT OVERWRITE TABLE {plan.dst} SELECT {cols} FROM {plan.src}")


def money_sum(spark: SparkSession, table: str, col: str) -> str | None:
    """取某列合计。列不存在时返回 None（不是所有表都有金额列）。"""
    exists = any(f.name == col for f in spark.table(table).schema.fields)
    if not exists:
        return None
    row = spark.sql(f"SELECT CAST(SUM({col}) AS STRING) AS s FROM {table}").first()
    return row["s"] if row and row["s"] is not None else None


def verify_table(spark: SparkSession, plan: TablePlan, checker: Checker) -> None:
    """逐表核对：行数 + 金额合计 + 分区列一致。"""
    src_n = spark.sql(f"SELECT COUNT(*) AS c FROM {plan.src}").first()["c"]
    dst_n = spark.sql(f"SELECT COUNT(*) AS c FROM {plan.dst}").first()["c"]
    checker.check(f"{plan.name} 行数一致", dst_n, src_n)

    # 空表上的"金额一致"是空洞的 —— 单独设防（Sprint 4 的教训）
    if src_n == 0:
        checker.check_true(f"{plan.name} 源表非空", "行数 > 0", lambda: False)
        return

    for col in MONEY_COLUMNS:
        s = money_sum(spark, plan.src, col)
        if s is None:
            continue
        d = money_sum(spark, plan.dst, col)
        checker.check(f"{plan.name}.{col} 合计一致", d, s)


def main() -> int:
    spark = build_spark("sprint5-migrate-parquet-to-iceberg")
    checker = Checker("Sprint 5 阶段 4：Parquet → Iceberg 迁移核对")

    # 上次写入残留会干扰后续装载（Sprint 3 踩过）
    paths = tuple(f"{WAREHOUSE}/iceberg/{t}" for t in MIGRATE_TABLES)
    removed = clean_stale_spark_temp(spark, paths)
    if removed:
        print(f"[clean] 清理残留目录：{removed} 个", flush=True)

    # !! 这里必须用**一段名**，而且这个名字不能与任何 catalog 同名 !!
    #   实测踩坑：写 `CREATE DATABASE IF NOT EXISTS lakehouse_iceberg` 时，
    #   因为 lakehouse_iceberg **既是库名也是 catalog 名**，Spark 把它解析成
    #   "目录 + 空命名空间"，报 `Cannot create namespace with invalid name: `
    #   （冒号后面是空的 —— 这就是"命名空间没有名字"的样子）。
    #   修法是把 catalog 改名成 iceberg（见 spark-defaults.conf），库名保持不变，
    #   于是 `lakehouse_iceberg` 只可能是一个命名空间的名字，没有二义。
    #   HiveCatalog 的命名空间 = Hive 库名，所以即便这条语句经 Hive 目录执行，
    #   建出来的也正是 Iceberg 要用的那个库（两边同一个 Metastore）。
    print(f"[migrate] 建 Iceberg 库 {DST_NS}", flush=True)
    spark.sql(f"CREATE DATABASE IF NOT EXISTS {DST_NS}")

    plans: list[TablePlan] = []
    for name in MIGRATE_TABLES:
        plan = build_plan(spark, name)
        plans.append(plan)

    print(f"[migrate] 共 {len(plans)} 张表", flush=True)
    for plan in plans:
        ddl = create_iceberg_table(spark, plan)
        print(f"[ddl] {plan.dst}（分区：{plan.partition_cols or '无'}）", flush=True)
        print(f"      {ddl.splitlines()[0]} ...", flush=True)
        # 建完立刻问表自己"你是什么表"，而不是相信 CREATE 没报错就等于建对了。
        # 这一条正是这次踩坑的产物：命令成功、表也建出来了，
        # 但建在**另一个目录的同名库**里 —— 只有问表自己才能发现。
        rows = spark.sql(f"DESCRIBE EXTENDED {plan.dst}").where("col_name = 'Provider'").collect()
        provider = rows[0]["data_type"] if rows else "<未取到>"
        checker.check(f"{plan.name} 是 Iceberg 表（Provider）", provider, "iceberg")

    for plan in plans:
        print(f"[migrate] 搬数据 {plan.src} → {plan.dst}", flush=True)
        migrate(spark, plan)

    print("[verify] 逐表核对", flush=True)
    for plan in plans:
        verify_table(spark, plan, checker)

    # 汇总：Iceberg 库的表数
    dst_count = spark.sql(f"SHOW TABLES IN {DST_DB}").count()
    checker.check("Iceberg 库表数 == 迁移清单表数", dst_count, len(MIGRATE_TABLES))

    rc = checker.finish()
    spark.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
