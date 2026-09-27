-- 金额关系：支付金额 <= 订单金额（不允许超付）
-- 返回 1 表示业务不变量成立
SELECT (MIN(p) <= MIN(o)) AS ok FROM (SELECT (SELECT SUM(amount) FROM ecommerce.dwd_trade_order_detail) AS o, (SELECT SUM(amount) FROM ecommerce.dwd_trade_payment_detail WHERE payment_status = 'SUCCESS') AS p) t;
