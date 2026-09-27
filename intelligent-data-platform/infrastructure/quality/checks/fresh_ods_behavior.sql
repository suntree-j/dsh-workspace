-- 新鲜度：贴源归档层（行为事件）最新 dt 距现在的滞后小时数
SELECT ROUND(TIMESTAMPDIFF(MINUTE, (SELECT MAX(dt) FROM ecommerce.dwd_traffic_behavior_detail), NOW()) / 60, 2) AS lag_hours;
