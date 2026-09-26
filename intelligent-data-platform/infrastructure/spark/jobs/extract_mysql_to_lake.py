"""Sprint 2：MySQL → 湖仓（ODS 层）全量抽取作业。

运行方式（服务器上，仓库根目录）：
    bash scripts/submit-offline-job.sh

它做什么：
    1. 用 Spark JDBC **并行**读 ecommerce 库的 5 张业务表；
    2. 按 sql/hive/01_ods_tables.sql 的定义建/复用 Hive 外部表（Parquet on S3A）；
    3. 把数据以 Parquet 写入 s3a://lakehouse/warehouse/ods/<表>/（overwrite，幂等）；
    4. **逐表对账**：把湖仓行数与 MySQL 行数比对，不一致就退出码非 0。

为什么作业自己做对账：
    离线链路的价值在于"用另一套引擎算出同一个数"，如果作业只是"跑完不报错"，
    就无法回答"到底搬全了没有"。把对账放进作业里，让每次运行都自带证据；
    失败即失败（AGENTS.md 第 10.3 节），不做任何"看起来成功"的兜底。

为什么用 DECIMAL 而不是 double 读金额：
    目标是与实时链路、MySQL 精确对账（到分）。double 会在超过 2^53 分之后丢精度，
    本项目金额最大量级约 5e7，虽然还没到边界，但**口径一致性要求类型先一致**。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DecimalType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# ------------------------------------------------------------
# 表规格：源表 → ODS 表
#
# 每张表声明：
#   source        MySQL 表名
#   target        Hive 表名（lakehouse 库）
#   location      S3A 路径（与 sql/hive/01_ods_tables.sql 保持一致）
#   pk            用于 JDBC 并行切分的数值主键
#   schema        显式 schema —— 不使用 JDBC 自动推断
#   count_sql     对账用的 MySQL 计数 SQL（允许带 WHERE）
# ------------------------------------------------------------
@dataclass(frozen=True)
class TableSpec:
    source: str
    target: str
    location: str
    pk: str
    schema: StructType
    count_sql: str


MONEY = DecimalType(18, 2)
TS = TimestampType()

TABLE_SPECS: list[TableSpec] = [
    TableSpec(
        source="user",
        target="ods_user",
        location="s3a://lakehouse/warehouse/ods/user",
        pk="user_id",
        schema=StructType([
            StructField("user_id", LongType(), False),
            StructField("username", StringType(), True),
            StructField("gender", IntegerType(), True),
            StructField("age", IntegerType(), True),
            StructField("province", StringType(), True),
            StructField("city", StringType(), True),
            StructField("user_level", StringType(), True),
            StructField("register_time", TS, True),
            StructField("update_time", TS, True),
        ]),
        count_sql="SELECT COUNT(*) FROM ecommerce.user",
    ),
    TableSpec(
        source="product",
        target="ods_product",
        location="s3a://lakehouse/warehouse/ods/product",
        pk="product_id",
        schema=StructType([
            StructField("product_id", LongType(), False),
            StructField("product_name", StringType(), True),
            StructField("category_id", LongType(), True),
            StructField("category_name", StringType(), True),
            StructField("brand", StringType(), True),
            StructField("price", MONEY, True),
            StructField("cost", MONEY, True),
            StructField("status", IntegerType(), True),
            StructField("create_time", TS, True),
            StructField("update_time", TS, True),
        ]),
        count_sql="SELECT COUNT(*) FROM ecommerce.product",
    ),
    TableSpec(
        source="orders",
        target="ods_orders",
        location="s3a://lakehouse/warehouse/ods/orders",
        pk="order_id",
        schema=StructType([
            StructField("order_id", LongType(), False),
            StructField("user_id", LongType(), True),
            StructField("product_id", LongType(), True),
            StructField("quantity", IntegerType(), True),
            StructField("amount", MONEY, True),
            StructField("status", StringType(), True),
            StructField("create_time", TS, True),
            StructField("pay_time", TS, True),
            StructField("update_time", TS, True),
        ]),
        count_sql="SELECT COUNT(*) FROM ecommerce.orders",
    ),
    TableSpec(
        source="payment",
        target="ods_payment",
        location="s3a://lakehouse/warehouse/ods/payment",
        pk="payment_id",
        schema=StructType([
            StructField("payment_id", LongType(), False),
            StructField("order_id", LongType(), True),
            StructField("user_id", LongType(), True),
            StructField("amount", MONEY, True),
            StructField("payment_method", StringType(), True),
            StructField("payment_status", StringType(), True),
            StructField("payment_time", TS, True),
        ]),
        count_sql="SELECT COUNT(*) FROM ecommerce.payment",
    ),
    TableSpec(
        source="refund",
        target="ods_refund",
        location="s3a://lakehouse/warehouse/ods/refund",
        pk="refund_id",
        schema=StructType([
            StructField("refund_id", LongType(), False),
            StructField("order_id", LongType(), True),
            StructField("user_id", LongType(), True),
            StructField("refund_amount", MONEY, True),
            StructField("refund_reason", StringType(), True),
            StructField("refund_status", StringType(), True),
            StructField("refund_time", TS, True),
        ]),
        count_sql="SELECT COUNT(*) FROM ecommerce.refund",
    ),
]

# JDBC 连接参数（口令由 spark-submit 通过 --conf 注入，见 submit-offline-job.sh）
JDBC_URL = "jdbc:mysql://mysql:3306/ecommerce?useSSL=false&allowPublicKeyRetrieval=true&characterEncoding=UTF-8"
JDBC_DRIVER = "com.mysql.cj.jdbc.Driver"
JDBC_PARALLELISM = 4


def build_spark() -> SparkSession:
    """创建 SparkSession（配置来自 spark-defaults.conf + 提交参数）。"""
    return (
        SparkSession.builder.appName("sprint2-extract-mysql-to-lakehouse")
        .config("spark.sql.catalogImplementation", "hive")
        .enableHiveSupport()
        .getOrCreate()
    )


def jdbc_options(spark: SparkSession) -> dict[str, str]:
    """JDBC 连接参数，其中 user/password 由提交脚本通过 --conf 传入。"""
    conf = spark.sparkContext.getConf()
    return {
        "url": JDBC_URL,
        "driver": JDBC_DRIVER,
        "user": conf.get("spark.mysql.user", "root"),
        "password": conf.get("spark.mysql.password", ""),
    }


def read_mysql(spark: SparkSession, spec: TableSpec, options: dict[str, str]) -> DataFrame:
    """并行读取一张 MySQL 表，并把类型对齐到 ODS 定义。

    并行切分依赖数值主键（本项目所有表都有 BIGINT 主键），
    否则 Spark 只能用单分区顺序拉取，5 张表虽然只有 1.4 万行不致命，
    但这是**可复制的正确做法**，将来数据量上来不用改作业。
    """
    bounds = spark.read.jdbc(
        url=options["url"],
        table=f"(SELECT MIN({spec.pk}) AS lo, MAX({spec.pk}) AS hi FROM ecommerce.{spec.source}) t",
        properties={"driver": options["driver"], "user": options["user"], "password": options["password"]},
    ).first()
    lower = int(bounds["lo"] or 0)
    upper = int(bounds["hi"] or 0)

    reader = (
        spark.read.format("jdbc")
        .option("url", options["url"])
        .option("dbtable", f"ecommerce.{spec.source}")
        .option("driver", options["driver"])
        .option("user", options["user"])
        .option("password", options["password"])
        .option("fetchsize", "1000")
    )
    if upper > lower:
        reader = (
            reader.option("partitionColumn", spec.pk)
            .option("lowerBound", lower)
            .option("upperBound", upper)
            .option("numPartitions", JDBC_PARALLELISM)
        )

    df = reader.load()
    # 显式按 ODS schema 选列 + 转型：源表加字段时作业不会静默多带列，
    # 也不会因为 JDBC 推断出 tinyint 之类的类型而与 Hive 表定义不一致。
    casts = []
    for field in spec.schema.fields:
        casts.append(F.col(field.name).cast(field.dataType).alias(field.name))
    return df.select(*casts)


def create_tables(spark: SparkSession, ddl_path: str) -> None:
    """执行 sql/hive/01_ods_tables.sql（按分号切分，跳过注释行）。"""
    with open(ddl_path, encoding="utf-8") as handle:
        content = handle.read()

    statements: list[str] = []
    buffer: list[str] = []
    for raw in content.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("--"):
            continue
        buffer.append(line)
        if line.endswith(";"):
            statements.append("\n".join(buffer).rstrip(";").strip())
            buffer = []
    if buffer:
        statements.append("\n".join(buffer).strip())

    print(f"[ddl] 执行 {len(statements)} 条建表语句 ← {ddl_path}", flush=True)
    for statement in statements:
        first_line = " ".join(statement.split())[:90]
        spark.sql(statement)
        print(f"[ddl]   OK  {first_line} ...", flush=True)


def reconcile(spark: SparkSession, spec: TableSpec, options: dict[str, str]) -> tuple[int, int]:
    """返回 (湖仓行数, MySQL 行数)。"""
    lake_count = spark.sql(f"SELECT COUNT(*) AS c FROM lakehouse.{spec.target}").first()["c"]
    mysql_count = spark.read.jdbc(
        url=options["url"],
        table=f"({spec.count_sql}) t",
        properties={"driver": options["driver"], "user": options["user"], "password": options["password"]},
    ).first()[0]
    return int(lake_count), int(mysql_count)


def main() -> int:
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")
    options = jdbc_options(spark)

    ddl_path = spark.sparkContext.getConf().get("spark.jobs.ddl", "/opt/jobs/01_ods_tables.sql")
    create_tables(spark, ddl_path)

    print("", flush=True)
    print("[extract] 开始抽取（Parquet / S3A，overwrite 幂等）", flush=True)
    results: list[tuple[str, int, int]] = []
    for spec in TABLE_SPECS:
        df = read_mysql(spark, spec, options)
        rows = df.count()
        (
            df.write.mode("overwrite")
            .option("compression", "snappy")
            .parquet(spec.location)
        )
        print(f"[extract]   {spec.source:<9} → {spec.location}  ({rows} 行)", flush=True)
        results.append((spec.target, rows, -1))

    print("", flush=True)
    print("[reconcile] 湖仓 vs MySQL 逐表对账", flush=True)
    failed = 0
    for index, spec in enumerate(TABLE_SPECS):
        lake_count, mysql_count = reconcile(spark, spec, options)
        flag = "OK  " if lake_count == mysql_count else "差异"
        if lake_count != mysql_count:
            failed += 1
        print(
            f"[reconcile]   {flag} {spec.target:<12} 湖仓 {lake_count:>6} 行  "
            f"MySQL {mysql_count:>6} 行",
            flush=True,
        )

    print("", flush=True)
    if failed:
        print(f"[result] 失败：{failed} 张表行数不一致 —— 离线链路不可信，必须先查原因", flush=True)
        spark.stop()
        return 1
    print(f"[result] 成功：{len(TABLE_SPECS)} 张 ODS 表全部与 MySQL 精确一致", flush=True)
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
