-- 新鲜度：交易域离线 ADS（1 分钟）最新窗口距现在的滞后小时数
SELECT ROUND(TIMESTAMPDIFF(MINUTE, (SELECT MAX(window_start) FROM lakehouse_ads.ads_batch_trade_1m), NOW()) / 60, 2) AS lag_hours;
