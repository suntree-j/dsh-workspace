-- ============================================================
-- Sprint 1 — Flink SQL：DWD 作业（清洗 + 补维 + 统一字段）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_1.md 第 3.2 节
--
-- 这四条 INSERT 会各自提交为一个**常驻流作业**。
-- 时间字段处理：
--   event_time 为 TIMESTAMP(3)，直接写出即为
--   'yyyy-MM-dd HH:mm:ss' 文本（与 Doris DATETIME 一致，不带时区偏移），
--   因此 Doris Routine Load 按 Asia/Shanghai 直接解析即可。
--   dt 用 CAST(... AS DATE) 得到 'yyyy-MM-dd'，与 Doris DATE 一致。
-- ============================================================

-- ------------------------------------------------------------
-- 1) 订单明细（只取订单创建事件）
-- ------------------------------------------------------------
INSERT INTO sink_dwd_trade_order_detail
SELECT
    order_id,
    user_id,
    product_id,
    category_name,
    brand,
    quantity,
    amount,
    event_id,
    event_time,
    CAST(CAST(event_time AS DATE) AS STRING) AS dt
FROM src_order_event
WHERE event_type = 'ORDER_CREATED';

-- ------------------------------------------------------------
-- 2) 支付明细（成功与失败都保留，由 payment_status 区分）
-- ------------------------------------------------------------
INSERT INTO sink_dwd_trade_payment_detail
SELECT
    payment_id,
    order_id,
    user_id,
    amount,
    payment_method,
    CASE
        WHEN event_type = 'PAYMENT_SUCCESS' THEN 'SUCCESS'
        WHEN event_type = 'PAYMENT_FAILED'  THEN 'FAILED'
        ELSE 'UNKNOWN'
    END AS payment_status,
    event_id,
    event_time,
    CAST(CAST(event_time AS DATE) AS STRING) AS dt
FROM src_payment_event;

-- ------------------------------------------------------------
-- 3) 退款明细
-- ------------------------------------------------------------
INSERT INTO sink_dwd_trade_refund_detail
SELECT
    refund_id,
    order_id,
    user_id,
    refund_amount,
    event_id,
    event_time,
    CAST(CAST(event_time AS DATE) AS STRING) AS dt
FROM src_refund_event
WHERE event_type = 'REFUND_CREATED';

-- ------------------------------------------------------------
-- 4) 行为明细
-- ------------------------------------------------------------
INSERT INTO sink_dwd_traffic_behavior_detail
SELECT
    event_id,
    event_type,
    user_id,
    product_id,
    category_name,
    device,
    province,
    event_time,
    CAST(CAST(event_time AS DATE) AS STRING) AS dt
FROM src_behavior_event;
