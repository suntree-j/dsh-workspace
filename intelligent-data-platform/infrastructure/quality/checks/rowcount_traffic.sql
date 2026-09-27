-- 流量域非空守卫：行为明细 + 流量 ADS 三表（Sprint 5 刚建好，必须有守卫）
-- 返回 1 = 全部非空
SELECT (MIN(a) > 0 AND MIN(b) > 0 AND MIN(c) > 0 AND MIN(d) > 0) AS ok FROM (SELECT (SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail) AS a, (SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1m) AS b, (SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1d) AS c, (SELECT COUNT(*) FROM lakehouse_ads.ads_reconcile_traffic_1m) AS d) t;
