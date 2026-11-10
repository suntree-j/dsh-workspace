-- 推进滞后：流量域离线 ADS（1 分钟）落后实时 ADS 的小时数
--
-- 语义与 fresh_ads_trade_1m.sql 一致：比"批层 vs 流层"，不与 NOW() 比。
-- 原因见该文件的注释——数据集保留原始历史事件时间戳，与系统时钟比恒红。
SELECT ROUND(TIMESTAMPDIFF(MINUTE, (SELECT MAX(window_start) FROM lakehouse_ads.ads_traffic_1m), (SELECT MAX(window_start) FROM ecommerce.ads_realtime_traffic_1m)) / 60, 2) AS lag_hours;
