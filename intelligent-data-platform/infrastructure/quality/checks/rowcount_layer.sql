-- 分层非空守卫：ODS(归档) / DWD / DWS / ADS 各层代表表
-- 返回 1 = 四层都还有数据
SELECT (MIN(a) > 0 AND MIN(b) > 0 AND MIN(c) > 0 AND MIN(d) > 0) AS ok FROM (SELECT (SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail) AS a, (SELECT COUNT(*) FROM ecommerce.dws_traffic_overview_1m) AS b, (SELECT COUNT(*) FROM ecommerce.ads_realtime_traffic_1m) AS c, (SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1d) AS d) t;
