-- 漏斗逐级收窄（AGENTS.md 8.2）：VIEW > CLICK > CART > BUY
-- 返回 1 表示断言成立，0 表示被违反
-- 说明：FAVORITE 是旁支，不参与逐级比较（它可以从任意一级跳过来）
SELECT (MIN(v) > MIN(c) AND MIN(c) > MIN(ca) AND MIN(ca) > MIN(b)) AS funnel_ok FROM (SELECT (SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail WHERE event_type = 'VIEW') AS v, (SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail WHERE event_type = 'CLICK') AS c, (SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail WHERE event_type = 'CART') AS ca, (SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail WHERE event_type = 'BUY') AS b) t;
