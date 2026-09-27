-- 新鲜度：流量域离线 ADS（1 分钟）最新窗口距现在的滞后小时数
-- 为什么用"滞后量"而不是"必须是今天"：数据是一次性生成的，不是每天持续生产，
--   "最新分区 = 今天"在这台机器上恒不成立（见 checks.conf 的新鲜度说明）。
-- 返回滞后小时数（保留 2 位小数），必须 < 阈值
SELECT ROUND(TIMESTAMPDIFF(MINUTE, (SELECT MAX(window_start) FROM lakehouse_ads.ads_traffic_1m), NOW()) / 60, 2) AS lag_hours;
