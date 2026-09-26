-- ============================================================
-- Sprint 1 — Flink SQL：Kafka 事件源表（ODS 接入）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_1.md 第 3.1 / 5 节
--
-- 设计要点：
--   1) 时间语义使用**事件时间**（event_time），并定义 watermark，
--      保证乱序与重放时结果一致（见 sql/metadata/metrics.md 第 1 节）。
--   2) 从 earliest-offset 开始消费，便于本地重放验证。
--   3) JSON 使用 flink-json 格式（镜像内已含）。
--   4) order_event 中带有冗余维度 category_name / brand（生成器写入），
--      因此无需 join 维表即可产出类目维度指标。
--
-- 事件格式定义见 sql/metadata/kafka_topics.md
--
-- !! 并行度必须显式设置 !!
--   Sprint 1 共有 8 个常驻作业（4 个 DWD + 4 个指标）。
--   Flink 的 parallelism.default 默认为 3（与 Kafka 分区数一致），
--   但**每个作业至少占用 parallelism 个 slot**，8 个作业 × 3 = 24 个 slot，
--   远超 TaskManager 能提供的数量，后提交的作业会失败：
--     NoResourceAvailableException:
--       Could not acquire the minimum required resources
--   症状是部分作业 RUNNING、其余直接 FAILED，且 JobManager 日志里
--   看不到 SQL 语法错误（因为根本不是语法问题）。
--   因此这里统一把并行度设为 1。
-- ============================================================

SET 'parallelism.default' = '1';

-- ------------------------------------------------------------
-- 订单事件
-- ------------------------------------------------------------
CREATE TABLE src_order_event (
    event_id      STRING,
    event_type    STRING,
    order_id      BIGINT,
    user_id       BIGINT,
    product_id    BIGINT,
    quantity      INT,
    amount        DECIMAL(18, 2),
    category_name STRING,
    brand         STRING,
    event_time    TIMESTAMP(3),
    -- 允许 5 秒乱序；超过水位的迟到数据在窗口侧由 allowedLateness 处理
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'order_event',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'flink-sprint1-order',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.fail-on-missing-field' = 'false',
    'json.ignore-parse-errors' = 'true'
);

-- ------------------------------------------------------------
-- 支付事件
-- ------------------------------------------------------------
CREATE TABLE src_payment_event (
    event_id       STRING,
    event_type     STRING,
    payment_id     BIGINT,
    order_id       BIGINT,
    user_id        BIGINT,
    amount         DECIMAL(18, 2),
    payment_method STRING,
    event_time     TIMESTAMP(3),
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'payment_event',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'flink-sprint1-payment',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.fail-on-missing-field' = 'false',
    'json.ignore-parse-errors' = 'true'
);

-- ------------------------------------------------------------
-- 退款事件
-- ------------------------------------------------------------
CREATE TABLE src_refund_event (
    event_id      STRING,
    event_type    STRING,
    refund_id     BIGINT,
    order_id      BIGINT,
    user_id       BIGINT,
    refund_amount DECIMAL(18, 2),
    event_time    TIMESTAMP(3),
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'refund_event',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'flink-sprint1-refund',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.fail-on-missing-field' = 'false',
    'json.ignore-parse-errors' = 'true'
);

-- ------------------------------------------------------------
-- 行为事件
-- ------------------------------------------------------------
CREATE TABLE src_behavior_event (
    event_id      STRING,
    event_type    STRING,
    user_id       BIGINT,
    product_id    BIGINT,
    device        STRING,
    province      STRING,
    category_name STRING,
    event_time    TIMESTAMP(3),
    WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'behavior_event',
    'properties.bootstrap.servers' = 'kafka:9092',
    'properties.group.id' = 'flink-sprint1-behavior',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.fail-on-missing-field' = 'false',
    'json.ignore-parse-errors' = 'true'
);
