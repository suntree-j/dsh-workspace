-- 新鲜度：流量域离线 ADS（1 天）最新分区日期距现在的滞后小时数
-- 取 dt 当天 00:00 作为数据时间点（天分区的语义就是那一天）
SELECT ROUND(TIMESTAMPDIFF(MINUTE, (SELECT MAX(dt) FROM lakehouse_ads.ads_traffic_1d), NOW()) / 60, 2) AS lag_hours;
