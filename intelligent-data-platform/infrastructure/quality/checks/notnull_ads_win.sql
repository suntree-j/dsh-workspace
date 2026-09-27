-- 离线 ADS 窗口字段非空：window_start / window_end（1m 两张表）
SELECT (SELECT COUNT(*) FROM lakehouse_ads.ads_batch_trade_1m WHERE window_start IS NULL OR window_end IS NULL) + (SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1m WHERE window_start IS NULL OR window_end IS NULL) AS null_win;
