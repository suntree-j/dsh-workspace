-- 金额关系：退款金额 <= 支付金额（不允许超退）
-- 返回 1 表示业务不变量成立
SELECT (MIN(r) <= MIN(p)) AS ok FROM (SELECT (SELECT SUM(amount) FROM ecommerce.dwd_trade_payment_detail WHERE payment_status = 'SUCCESS') AS p, (SELECT SUM(refund_amount) FROM ecommerce.dwd_trade_refund_detail) AS r) t;
