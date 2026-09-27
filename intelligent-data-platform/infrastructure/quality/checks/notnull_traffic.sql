-- 流量域关键字段非空：event_id / user_id / event_time / event_type / dt
-- 注：product_id 与 category_name **不计入** —— 实时侧 product 维表是 lookup join 补的，
--     category_name 为空是已知且可接受的现象（见 05_traffic_dwd.sql 的说明）。
--     把已知可空列写进非空校验，只会造出一条恒红的假警报。
SELECT COUNT(*) AS null_critical FROM ecommerce.dwd_traffic_behavior_detail WHERE event_id IS NULL OR user_id IS NULL OR event_time IS NULL OR event_type IS NULL OR dt IS NULL;
