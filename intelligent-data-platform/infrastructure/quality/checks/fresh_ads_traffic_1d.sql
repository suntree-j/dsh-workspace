-- 推进滞后：流量域离线 ADS（1 天）最新分区落后实时链路的小时数
--
-- 取 dt 当天 00:00 与实时 1m 窗口所属日期的 00:00 作比较，
-- 因此结果总是 24 的整数倍：滞后 1 天 = 24h（通过 48h 阈值），2 天 = 48h（不通过）。
-- 不与 NOW() 比的原因见 fresh_ads_trade_1m.sql。
SELECT ROUND(TIMESTAMPDIFF(MINUTE, (SELECT MAX(dt) FROM lakehouse_ads.ads_traffic_1d), (SELECT CAST(MAX(window_start) AS DATE) FROM ecommerce.ads_realtime_traffic_1m)) / 60, 2) AS lag_hours;
