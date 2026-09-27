-- 行为枚举合法：event_type 必须在白名单内（口径见 sql/metadata/metrics.md 第 3 节）
-- 返回白名单**之外**的行数，必须为 0
SELECT COUNT(*) AS invalid_rows FROM ecommerce.dwd_traffic_behavior_detail WHERE event_type IS NOT NULL AND event_type NOT IN ('VIEW', 'CLICK', 'CART', 'FAVORITE', 'BUY');
