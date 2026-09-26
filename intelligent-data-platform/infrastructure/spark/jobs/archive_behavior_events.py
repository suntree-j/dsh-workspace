"""Sprint 4：Kafka 行为事件 → 湖仓 ODS 归档作业。

运行方式（服务器上，仓库根目录）：
    bash scripts/submit-offline-job.sh --stage archive
    # 或走错峰模式（推荐，会自动暂停实时链路腾内存）
    bash scripts/batch-mode.sh --stage archive

它解决什么问题
--------------
这是 Sprint 3 明确记录下来的**设计缺口**：

    交易域  MySQL 有事实表          →  离线已可算、已对账  ✅
    流量域  仅 Kafka 有行为事件      →  离线**无源可算**    ⚠️

因此在 Sprint 4 之前，批流对账只覆盖交易域；流量域（UV / PV / 转化率）
无法参与，Agent 回答相关问题时会如实说明"这类指标不在对账范围内"。
本作业把 Kafka 的 `behavior_event` 归档进湖仓 ODS，
流量域从此也能离线计算并与实时链路逐窗口对账。

它做什么
--------
    1. 确保 ODS 表存在（sql/hive/01_ods_tables.sql 的 ods_behavior_event）；
    2. 从 Kafka **批读**（earliest → latest）整个 topic；
    3. 按 behavior_event 信封解析 JSON，用**显式 schema**（不做类型推断）；
    4. 按 event_time 推导 dt 分区，写入 ODS（动态分区覆盖 = 幂等）；
    5. **作业内自检**：行数与 Kafka 的实际消息数逐分区核对，
       外加漏斗单调性、枚举合法性、关键字段非空。

为什么用 Spark 批读而不是 Flink 流式写湖
----------------------------------------
    本项目"实时"已由 Flink → Doris 承担；湖仓的角色是**准确、可回溯的
    离线副本**。批读归档完全满足这个定位，且**不新增常驻进程** ——
    在一台可用内存只有约 2.5 GB 的机器上，这是决定性的。
    Flink 流式写湖要多养一个作业（+1~2 GB 常驻），还要处理小文件问题；
    Spark Structured Streaming 同样常驻且引入 checkpoint 管理复杂度。

幂等的实现方式
--------------
    build_spark() 里设了 spark.sql.sources.partitionOverwriteMode=dynamic，
    因此 INSERT OVERWRITE 只覆盖**本次数据涉及的 dt 分区**，其它分区不动。
    重跑行数不变（验收会实测这一点）。
"""

from __future__ import annotations

import sys

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    LongType,
    StringType,
    StructField,
    StructType,
)

from _common import (
    Checker,
    build_spark,
    clean_stale_spark_temp,
    describe_columns,
    ensure_tables,
    exact_distinct,
)

TOPIC = "behavior_event"
ODS_TABLE = "lakehouse.ods_behavior_event"
ODS_PATH = "s3a://lakehouse/warehouse/ods/behavior_event"

#: 行为事件的合法枚举，与 sql/metadata/kafka_topics.md 第 8.1 节一致。
#: 作业会把实际出现的取值与它比对 —— 出现未登记的枚举要立刻发现，
#: 而不是等到下游指标算错。
VALID_EVENT_TYPES = ("VIEW", "CLICK", "CART", "BUY", "FAVORITE", "SEARCH")

#: 主漏斗（用于单调性检查）。FAVORITE 是旁支、SEARCH 是入口，都不在其中。
FUNNEL = ("VIEW", "CLICK", "CART", "BUY")

#: 显式 schema。
#:
#: !! 为什么不用 inferSchema !!
#:   推断会把 product_id 这种"可为 null 的数值列"在某些批次里判成 string，
#:   导致写入类型与 Hive 表定义不一致；源端加字段时也会静默多带列。
#:   显式声明后，源端加字段作业不会变，类型也永远与建表 DDL 对齐。
EVENT_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), nullable=True),
        StructField("event_type", StringType(), nullable=True),
        StructField("user_id", LongType(), nullable=True),
        StructField("product_id", LongType(), nullable=True),
        StructField("device", StringType(), nullable=True),
        StructField("province", StringType(), nullable=True),
        # 事件时间是 ISO-8601 带时区（如 2026-09-26T10:05:00+08:00），
        # 先按 string 读进来，再用 to_timestamp 按同一时区解析 ——
        # 直接当 timestamp 读会因驱动/会话时区不同而产生偏移。
        StructField("event_time", StringType(), nullable=True),
    ]
)


def kafka_bootstrap(spark: SparkSession) -> str:
    """Kafka 地址。

    !! 必须是容器服务名 kafka:9092，不能用 localhost:19092 !!
        本作业跑在 spark-submit 容器里（AGENTS.md 6.2：
        容器间禁止使用 localhost）。.env 里的 localhost:19092 是给
        **宿主机**上的 Python 用的，在容器里会连到容器自己。
    """
    return spark.sparkContext.getConf().get("spark.jobs.kafkaBootstrap", "kafka:9092")


def read_kafka(spark: SparkSession) -> tuple[DataFrame, int]:
    """批读整个 topic，返回 (原始 DataFrame, Kafka 实际消息数)。

    第二个返回值是**独立于解析结果**的消息数 —— 用它和归档后的行数对账，
    才能证明"没有消息在解析环节被悄悄丢掉"。
    """
    raw = (
        spark.read.format("kafka")
        .option("kafka.bootstrap.servers", kafka_bootstrap(spark))
        .option("subscribe", TOPIC)
        # 批读模式下 earliest → latest 表示"把 topic 里现有的全读一遍"。
        # 每次都全量读，配合动态分区覆盖，天然幂等。
        .option("startingOffsets", "earliest")
        .option("endingOffsets", "latest")
        # topic 里有历史数据被清理时不要让作业直接崩，
        # 但下游有行数对账兜底，不会静默少数据。
        .option("failOnDataLoss", "false")
        .load()
    )
    return raw, raw.count()


def parse_events(raw: DataFrame) -> DataFrame:
    """JSON 解析 + 类型归一。解析失败的行不会静默丢弃，而是计入 bad 行由自检暴露。"""
    parsed = raw.select(
        F.from_json(F.col("value").cast("string"), EVENT_SCHEMA).alias("e")
    ).select("e.*")

    return parsed.select(
        F.col("event_id").cast("string").alias("event_id"),
        F.upper(F.trim(F.col("event_type"))).alias("event_type"),
        F.col("user_id").cast("long").alias("user_id"),
        F.col("product_id").cast("long").alias("product_id"),
        F.col("device").cast("string").alias("device"),
        F.col("province").cast("string").alias("province"),
        # 统一解析到 Asia/Shanghai，与 metrics.md 的时区约定一致。
        #
        # !! 不要传格式串 !!
        #   实测踩坑（Sprint 4，最难定位的一层）：原先写的是
        #       F.to_timestamp(F.col("event_time"), "yyyy-MM-dd'T'HH:mm:ssXXX")
        #   结果 **20000 行全部解析成 NULL**，进而被下游的
        #   `WHERE event_time IS NOT NULL` 全部筛掉，写进表 0 行。
        #
        #   原因：Spark 3.x 的 to_timestamp(str, fmt) 走 DateTimeFormatter，
        #   而格式串里的字面量 `T` **不是合法模式字母** —— 它不会抛错，
        #   而是静默解析失败、整列变 NULL。这类"不报错但全错"最难查：
        #   作业跑完、日志正常、自检里"无空值"还空洞地通过（空表里当然没空值）。
        #
        #   事件时间本身就是标准 ISO-8601（2026-09-26T10:05:00+08:00），
        #   Spark 原生即可解析，**不传格式串才是正确做法**。
        F.to_timestamp(F.col("event_time")).alias("event_time"),
    )


def write_ods(spark: SparkSession, events: DataFrame) -> None:
    """写 ODS。用 SQL 的 INSERT OVERWRITE + 动态分区，只覆盖本次涉及的 dt。"""
    with_dt = events.withColumn("dt", F.date_format(F.col("event_time"), "yyyy-MM-dd"))
    with_dt.createOrReplaceTempView("v_behavior_events")

    spark.sql(
        f"""
        INSERT OVERWRITE TABLE {ODS_TABLE} PARTITION (dt)
        SELECT event_id, event_type, user_id, product_id, device, province, event_time, dt
        FROM v_behavior_events
        WHERE event_time IS NOT NULL
        """
    )


def verify(spark: SparkSession, kafka_count: int, events: DataFrame) -> int:
    """作业内自检。返回退出码（0 = 全部通过）。"""
    checker = Checker("Sprint 4 归档自检（Kafka → 湖仓 ODS）")

    archived = spark.table(ODS_TABLE).count()

    # ---- 1. 行数：归档 == Kafka 实际消息数 ----
    # 这一条是"没有丢消息"的硬证据。注意解析环节会丢掉 event_time 为 null 的
    # 畸形行，所以这里同时报告差值，便于判断是"丢了"还是"过滤了畸形行"。
    checker.check("归档行数 == Kafka 消息数", archived, kafka_count)

    parsed_count = events.count()
    checker.check("可解析行数 == Kafka 消息数", parsed_count, kafka_count)

    # ---- 2. 关键字段非空 ----
    #
    # !! 断言必须打在**源数据**上，不能只打在目标表上 !!
    #   实测踩坑（Sprint 4）：原先这里查的是 {ODS_TABLE}。
    #   当写入实际发生 0 行时，目标表是空的，
    #   于是"0 行里没有空值" → 4 条检查**空洞地全部通过**，
    #   而真实情况是 20000 行全部解析失败、一条都没写进去。
    #   一份"全绿"的自检报告配一个空表 —— 这比直接报错更危险。
    #
    #   现在先断言源非空（防空区间假通过），再在源上查非空。
    checker.check_true(
        "源数据非空（防空区间假通过）",
        parsed_count > 0,
        f"源行数={parsed_count}",
    )

    src_nulls = events.agg(
        F.sum(F.col("event_id").isNull().cast("int")).alias("null_id"),
        F.sum(F.col("event_type").isNull().cast("int")).alias("null_type"),
        F.sum(F.col("user_id").isNull().cast("int")).alias("null_user"),
        F.sum(F.col("event_time").isNull().cast("int")).alias("null_time"),
    ).first()
    checker.check("源 event_id 无空值", int(src_nulls["null_id"] or 0), 0)
    checker.check("源 event_type 无空值", int(src_nulls["null_type"] or 0), 0)
    checker.check("源 user_id 无空值", int(src_nulls["null_user"] or 0), 0)
    checker.check("源 event_time 无空值", int(src_nulls["null_time"] or 0), 0)

    # ---- 3. 枚举合法性 ----
    # 出现未登记的取值说明数据生成器或上游改了协议，必须立刻发现
    actual_types = {
        r["event_type"]
        for r in spark.sql(f"SELECT DISTINCT event_type FROM {ODS_TABLE}").collect()
    }
    unexpected = sorted(actual_types - set(VALID_EVENT_TYPES))
    checker.check("事件类型全部合法", unexpected, [])

    # ---- 4. 主漏斗单调收窄 ----
    # 与 AGENTS.md 8.2 的"行为漏斗 count(VIEW) > count(CLICK) > count(CART) > count(BUY)"
    # 是同一个断言；漏斗反常说明数据生成或解析出了问题。
    counts = {
        r["event_type"]: r["c"]
        for r in spark.sql(
            f"SELECT event_type, COUNT(*) AS c FROM {ODS_TABLE} GROUP BY event_type"
        ).collect()
    }
    funnel_counts = [counts.get(t, 0) for t in FUNNEL]
    checker.check_true(
        "漏斗逐级收窄 VIEW>CLICK>CART>BUY",
        all(
            funnel_counts[i] > funnel_counts[i + 1]
            for i in range(len(funnel_counts) - 1)
        ),
        f"实际 {dict(zip(FUNNEL, funnel_counts))}",
    )

    # ---- 5. 分区列与事件时间一致 ----
    # dt 是由 event_time 推导的，若出现不一致说明写入逻辑有问题
    bad_dt = spark.sql(
        f"""
        SELECT COUNT(*) AS c FROM {ODS_TABLE}
        WHERE dt <> date_format(event_time, 'yyyy-MM-dd')
        """
    ).first()["c"]
    checker.check("dt 分区与 event_time 一致", bad_dt, 0)

    # ---- 6. user_id 在维表中存在（ODS 层的一致性约束）----
    # AGENTS.md / kafka_topics.md 第 9 节要求行为事件的 user_id 必须存在于 user 表
    orphan = spark.sql(
        f"""
        SELECT COUNT(*) AS c
        FROM {ODS_TABLE} b
        LEFT JOIN lakehouse.ods_user u ON b.user_id = u.user_id
        WHERE u.user_id IS NULL
        """
    ).first()["c"]
    checker.check("user_id 均存在于 ods_user", orphan, 0)

    # ---- 7. 表结构核对 ----
    cols = describe_columns(spark, ODS_TABLE)
    for name, dtype in (
        ("event_id", "string"),
        ("event_type", "string"),
        ("user_id", "bigint"),
        ("event_time", "timestamp"),
        ("dt", "string"),
    ):
        checker.check(f"列 {name} 类型为 {dtype}", cols.get(name), dtype)

    # 分区数（每个 dt 一个分区）—— 打印出来便于人工核对
    partitions = exact_distinct(spark, "dt", ODS_TABLE)
    print(f"[info] 归档覆盖 {partitions} 个 dt 分区", flush=True)

    return checker.finish()


def main() -> int:
    spark = build_spark("sprint4-archive-behavior-events")

    print("[archive] 确保 ODS 表存在（sql/hive/01_ods_tables.sql）", flush=True)
    ensure_tables(spark)

    # 上次写入残留的 .spark-staging-* 会被 Doris 的 **/*.parquet 递归读到，
    # 导致装载行数翻倍（Sprint 3 踩过），先清掉。
    removed = clean_stale_spark_temp(spark, (ODS_PATH,))
    if removed:
        print(f"[clean] 清理 Spark 写入残留目录：{removed} 个", flush=True)

    print(f"[archive] 从 Kafka 批读 topic={TOPIC}（earliest → latest）", flush=True)
    raw, kafka_count = read_kafka(spark)
    print(f"[archive] Kafka 实际消息数 = {kafka_count}", flush=True)

    if kafka_count == 0:
        # 空 topic 不算失败，但必须显式说出来，而不是"成功归档了 0 行"
        print("[archive] topic 为空，无可归档数据（这可能不是期望的结果）", flush=True)

    events = parse_events(raw).cache()
    print(f"[archive] 解析完成，有效行数 = {events.count()}", flush=True)

    print(f"[archive] 写入 {ODS_TABLE}（按 dt 动态分区覆盖，幂等）", flush=True)
    write_ods(spark, events)

    print("[archive] 开始自检", flush=True)
    rc = verify(spark, kafka_count, events)

    spark.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
