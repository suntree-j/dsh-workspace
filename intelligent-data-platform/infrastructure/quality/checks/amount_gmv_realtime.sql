-- 金额关系：实时 DWD SUM(amount) == 离线 ADS SUM(gmv)
-- 两边都是 DECIMAL(18,2)，**做精确等值比较**，不用容差（Sprint 1/3 的验收口径）
-- 返回差的绝对值，必须为 0
SELECT ABS((SELECT SUM(amount) FROM ecommerce.dwd_trade_order_detail) - (SELECT SUM(gmv) FROM lakehouse_ads.ads_batch_trade_1d)) AS diff_amount;
