-- 离线 ADS：1 分钟粒度汇总 == 1 天粒度汇总（交易 GMV/订单数 + 流量 PV）
--
-- !! 为什么 UV 不比 !!
--   UV 是"去重用户数"，**跨窗口不可加**（口径见 sql/metadata/metrics.md）。
--   SUM(1m 的 uv) 必然远大于 SUM(1d 的 uv)，把它们相比会造出一条恒红的假校验。
--   这里只比可加指标：gmv / order_cnt / pv。
-- 返回 3 个差的绝对值之和，必须为 0
SELECT ABS((SELECT SUM(gmv) FROM lakehouse_ads.ads_batch_trade_1m) - (SELECT SUM(gmv) FROM lakehouse_ads.ads_batch_trade_1d)) + ABS((SELECT SUM(order_cnt) FROM lakehouse_ads.ads_batch_trade_1m) - (SELECT SUM(order_cnt) FROM lakehouse_ads.ads_batch_trade_1d)) + ABS((SELECT SUM(pv) FROM lakehouse_ads.ads_traffic_1m) - (SELECT SUM(pv) FROM lakehouse_ads.ads_traffic_1d)) AS diff_total;
