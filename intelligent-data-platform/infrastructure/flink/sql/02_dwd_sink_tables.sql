-- ============================================================
-- Sprint 1 — Flink SQL：DWD 明细 sink（写回 Kafka）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_1.md 第 3.2 / 5 节
--
-- 数据流：
--   Kafka 事件 topic → Flink 清洗/裁剪 → Kafka DWD topic → Doris Routine Load
--
-- 为什么写回 Kafka 而不是直写 Doris：
--   见 SPRINT_1.md 第 2.1 节 —— 避免依赖 doris-flink-connector
--   （Doris/Flink/connector 三方版本绑定），改用 Doris 内置 Routine Load。
--
-- 说明：
--   - DWD 层职责是「清洗 + 补维 + 统一字段」，不做聚合；
--   - dt 由 event_time 派生，便于 Doris 侧按天过滤；
--   - sink 不带主键 → Flink 以 append 流写出（明细追加语义）。
--
-- !! 实测踩坑（务必保留结论） !!
--   现象：DWD 作业 FAILED，JobManager 日志显示
--     java.lang.NullPointerException
--       at StreamRecordTimestampInserter.processElement(StreamRecordTimestampInserter.java:50)
--       Could not forward element to next operator
--   SQL Gateway 只返回 "Failed to fetchResults"，看不到真实原因。
--
--   根因：Kafka sink 会把**第一个 TIMESTAMP(3) 列**当作记录时间戳
--   （record timestamp）写入。该列取值为 NULL 时，插入算子直接 NPE。
--   而 event_time 之所以是 NULL，是因为 Flink 的 JSON format
--   无法解析带时区偏移的 ISO-8601 字符串（'2025-07-16T13:42:44+08:00'），
--   又因 source 配置了 json.ignore-parse-errors='true'，
--   解析失败被**静默置为 NULL**。
--
--   修复：事件时间统一改为 'yyyy-MM-dd HH:mm:ss'
--   （见 data-generator/src/common.py 的 to_sql_datetime），
--   Flink 与 Doris 均可直接解析，且与 Asia/Shanghai 时区语义一致。
-- ============================================================

-- ------------------------------------------------------------
-- DWD sink：订单明细
-- ------------------------------------------------------------
CREATE TABLE sink_dwd_trade_order_detail (
    order_id      BIGINT,
    user_id       BIGINT,
    product_id    BIGINT,
    category_name STRING,
    brand         STRING,
    quantity      INT,
    amount        DECIMAL(18, 2),
    event_id      STRING,
    event_time    TIMESTAMP(3),
    dt            STRING
) WITH (
    'connector' = 'kafka',
    'topic' = 'dwd_trade_order_detail',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json',
    'json.encode.decimal-as-plain-number' = 'true'
    -- ↑ 用途：Flink 默认把 DECIMAL 写成科学计数法（2.8E+2），
    --   Doris JSON 解析器不认，会导致 Routine Load 全行失败。
);

-- ------------------------------------------------------------
-- DWD sink：支付明细
-- ------------------------------------------------------------
CREATE TABLE sink_dwd_trade_payment_detail (
    payment_id     BIGINT,
    order_id       BIGINT,
    user_id        BIGINT,
    amount         DECIMAL(18, 2),
    payment_method STRING,
    payment_status STRING,
    event_id       STRING,
    event_time     TIMESTAMP(3),
    dt             STRING
) WITH (
    'connector' = 'kafka',
    'topic' = 'dwd_trade_payment_detail',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json',
    'json.encode.decimal-as-plain-number' = 'true'
    -- ↑ 用途：Flink 默认把 DECIMAL 写成科学计数法（2.8E+2），
    --   Doris JSON 解析器不认，会导致 Routine Load 全行失败。
);

-- ------------------------------------------------------------
-- DWD sink：退款明细
-- ------------------------------------------------------------
CREATE TABLE sink_dwd_trade_refund_detail (
    refund_id     BIGINT,
    order_id      BIGINT,
    user_id       BIGINT,
    refund_amount DECIMAL(18, 2),
    event_id      STRING,
    event_time    TIMESTAMP(3),
    dt            STRING
) WITH (
    'connector' = 'kafka',
    'topic' = 'dwd_trade_refund_detail',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json',
    'json.encode.decimal-as-plain-number' = 'true'
    -- ↑ 用途：Flink 默认把 DECIMAL 写成科学计数法（2.8E+2），
    --   Doris JSON 解析器不认，会导致 Routine Load 全行失败。
);

-- ------------------------------------------------------------
-- DWD sink：行为明细
-- ------------------------------------------------------------
CREATE TABLE sink_dwd_traffic_behavior_detail (
    event_id      STRING,
    event_type    STRING,
    user_id       BIGINT,
    product_id    BIGINT,
    category_name STRING,
    device        STRING,
    province      STRING,
    event_time    TIMESTAMP(3),
    dt            STRING
) WITH (
    'connector' = 'kafka',
    'topic' = 'dwd_traffic_behavior_detail',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json',
    'json.encode.decimal-as-plain-number' = 'true'
    -- ↑ 用途：Flink 默认把 DECIMAL 写成科学计数法（2.8E+2），
    --   Doris JSON 解析器不认，会导致 Routine Load 全行失败。
);
