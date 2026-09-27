-- 流量域主键：event_id 的 NULL 数 + 丢失行数（算法理由见 pk_unique_trade.sql）
-- 返回 0 才算通过；正常值实测为 0
SELECT COUNT(*) - COUNT(DISTINCT event_id) + SUM(CASE WHEN event_id IS NULL THEN 1 ELSE 0 END) AS dup_or_null_pk FROM ecommerce.dwd_traffic_behavior_detail;
