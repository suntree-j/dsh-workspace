-- 层间一致：交易域 DWD ↔ 离线 ADS 下单计数（同层口径交叉验证）
-- 订单数在 DWD 明细与 ADS 窗口里必须是同一个数（窗口聚合是可加的，不应丢行）
-- 返回 0 = 一致
SELECT ABS((SELECT COUNT(*) FROM ecommerce.dwd_trade_order_detail) - (SELECT SUM(order_cnt) FROM lakehouse_ads.ads_batch_trade_1d)) AS diff_rows;
