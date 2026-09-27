-- ADS 窗口数 > 0（四个窗口粒度表）
-- 返回 1 = 都非空
SELECT (MIN(a) > 0 AND MIN(b) > 0 AND MIN(c) > 0 AND MIN(d) > 0) AS ok FROM (SELECT (SELECT COUNT(*) FROM lakehouse_ads.ads_batch_trade_1m) AS a, (SELECT COUNT(*) FROM lakehouse_ads.ads_batch_trade_1d) AS b, (SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1m) AS c, (SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1d) AS d) t;
