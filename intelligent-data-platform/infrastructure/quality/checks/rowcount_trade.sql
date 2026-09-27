-- 交易域非空守卫：订单 / 支付 / 退款明细 + 离线交易 ADS
-- 返回 1 = 全部非空；任一为 0 → 返回 0（让校验失败）
SELECT (MIN(o) > 0 AND MIN(p) > 0 AND MIN(r) > 0 AND MIN(b) > 0) AS ok FROM (SELECT (SELECT COUNT(*) FROM ecommerce.dwd_trade_order_detail) AS o, (SELECT COUNT(*) FROM ecommerce.dwd_trade_payment_detail) AS p, (SELECT COUNT(*) FROM ecommerce.dwd_trade_refund_detail) AS r, (SELECT COUNT(*) FROM lakehouse_ads.ads_batch_trade_1m) AS b) t;
