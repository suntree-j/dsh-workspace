-- ============================================================
-- Sprint 1 — Flink SQL：DWS / ADS 指标 sink 表定义
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_1.md 第 3.3 / 3.4 节
-- 口径：sql/metadata/metrics.md（唯一权威）
--
-- !! 为什么必须用 upsert-kafka 而不是普通 kafka（实测踩坑） !!
--   窗口聚合（TUMBLE + GROUP BY window_start）在 Flink 中产生的是
--   **update 流**（含 retract / upsert 消息），而普通 'kafka' sink
--   只支持 append 流。直接写会在编译期失败：
--     org.apache.flink.table.api.TableException:
--       Table sink '...' doesn't support consuming update changes
--       which is produced by node GroupAggregate(groupBy=[window_start], ...)
--
--   官方文档（Table API Connectors → Upsert Kafka）明确说明：
--   需要消费 retract/upsert 流时应使用 Upsert Kafka connector。
--
--   因此所有 DWS / ADS sink 表都：
--     1) connector = 'upsert-kafka'
--     2) 声明 PRIMARY KEY —— 同一窗口在迟到数据到达时会被更新，
--        以 window_start（+ 维度）为主键，Upsert Kafka 会把同 key
--        的更新折叠为一条最新值。
--     3) key.format / value.format 均为 json
--
--   这与 Doris 侧建模天然吻合：Doris 表同样是
--   UNIQUE KEY(window_start[, 维度]) + merge-on-write，
--   同窗口重复写入覆盖，因此整条链路对「窗口更新」是幂等的。
--
-- 关于 DECIMAL：
--   'value.json.encode.decimal-as-plain-number' = 'true' 让 DECIMAL 以普通数字
--   写出（否则可能被序列化为 2.8E+2 之类的科学计数法，
--   Doris 的 JSON 解析器对科学计数法兼容性不稳定）。
-- ============================================================

-- ------------------------------------------------------------
-- DWS sink：流量总览（主键 window_start）
-- ------------------------------------------------------------
CREATE TABLE sink_dws_traffic_overview_1m (
    window_start TIMESTAMP(3),
    window_end   TIMESTAMP(3),
    pv           BIGINT,
    uv           BIGINT,
    view_cnt     BIGINT,
    click_cnt    BIGINT,
    cart_cnt     BIGINT,
    favorite_cnt BIGINT,
    buy_cnt      BIGINT,
    PRIMARY KEY (window_start) NOT ENFORCED
) WITH (
    'connector' = 'upsert-kafka',
    'topic' = 'dws_traffic_overview_1m',
    'properties.bootstrap.servers' = 'kafka:9092',
    'key.format' = 'json',
    'value.format' = 'json',
    'value.json.encode.decimal-as-plain-number' = 'true'
);

-- ------------------------------------------------------------
-- ADS sink：实时交易总览（主键 window_start）
-- ------------------------------------------------------------
CREATE TABLE sink_ads_realtime_trade_1m (
    window_start         TIMESTAMP(3),
    window_end           TIMESTAMP(3),
    gmv                  DECIMAL(18, 2),
    order_cnt            BIGINT,
    order_user_cnt       BIGINT,
    avg_order_amount     DECIMAL(18, 2),
    payment_cnt          BIGINT,
    payment_amount       DECIMAL(18, 2),
    payment_fail_cnt     BIGINT,
    payment_success_rate DECIMAL(10, 4),
    refund_cnt           BIGINT,
    refund_amount        DECIMAL(18, 2),
    refund_rate          DECIMAL(10, 4),
    PRIMARY KEY (window_start) NOT ENFORCED
) WITH (
    'connector' = 'upsert-kafka',
    'topic' = 'ads_realtime_trade_1m',
    'properties.bootstrap.servers' = 'kafka:9092',
    'key.format' = 'json',
    'value.format' = 'json',
    'value.json.encode.decimal-as-plain-number' = 'true'
);

-- ------------------------------------------------------------
-- ADS sink：实时流量（主键 window_start）
-- ------------------------------------------------------------
CREATE TABLE sink_ads_realtime_traffic_1m (
    window_start TIMESTAMP(3),
    window_end   TIMESTAMP(3),
    uv           BIGINT,
    pv           BIGINT,
    view_cnt     BIGINT,
    click_cnt    BIGINT,
    cart_cnt     BIGINT,
    favorite_cnt BIGINT,
    buy_cnt      BIGINT,
    click_rate   DECIMAL(10, 4),
    cart_rate    DECIMAL(10, 4),
    buy_rate     DECIMAL(10, 4),
    PRIMARY KEY (window_start) NOT ENFORCED
) WITH (
    'connector' = 'upsert-kafka',
    'topic' = 'ads_realtime_traffic_1m',
    'properties.bootstrap.servers' = 'kafka:9092',
    'key.format' = 'json',
    'value.format' = 'json',
    'value.json.encode.decimal-as-plain-number' = 'true'
);

-- ------------------------------------------------------------
-- ADS sink：实时类目销售（主键 window_start + category_name）
--
-- 注意：Doris 侧 ads_realtime_category_1m 的 UNIQUE KEY 为
--   (window_start, category_name)，与此处主键一致。
-- ------------------------------------------------------------
CREATE TABLE sink_ads_realtime_category_1m (
    window_start     TIMESTAMP(3),
    category_name    STRING,
    window_end       TIMESTAMP(3),
    order_cnt        BIGINT,
    gmv              DECIMAL(18, 2),
    total_quantity   BIGINT,
    avg_order_amount DECIMAL(18, 2),
    PRIMARY KEY (window_start, category_name) NOT ENFORCED
) WITH (
    'connector' = 'upsert-kafka',
    'topic' = 'ads_realtime_category_1m',
    'properties.bootstrap.servers' = 'kafka:9092',
    'key.format' = 'json',
    'value.format' = 'json',
    'value.json.encode.decimal-as-plain-number' = 'true'
);
