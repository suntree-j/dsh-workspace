-- 离线 ADS 窗口主键：窗口键为 NULL 或窗口键不唯一的行数
-- 三张窗口表：交易 1m（key=window_start）、流量 1m（key=window_start）、流量 1d（key=dt）
-- 返回 0 才算通过；正常值实测为 0
SELECT (SELECT COUNT(*) FROM lakehouse_ads.ads_batch_trade_1m WHERE window_start IS NULL) + (SELECT COUNT(*) - COUNT(DISTINCT window_start) FROM lakehouse_ads.ads_batch_trade_1m) + (SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1m WHERE window_start IS NULL) + (SELECT COUNT(*) - COUNT(DISTINCT window_start) FROM lakehouse_ads.ads_traffic_1m) + (SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1d WHERE dt IS NULL) + (SELECT COUNT(*) - COUNT(DISTINCT dt) FROM lakehouse_ads.ads_traffic_1d) AS dup_or_null_key;
