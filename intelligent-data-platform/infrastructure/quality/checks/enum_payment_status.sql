-- 支付状态枚举合法：只允许 SUCCESS / FAILED
-- 支付成功率的分母 = 成功 + 失败，多一个取值就会把比率算歪
-- 返回非法取值行数，必须为 0
SELECT COUNT(*) AS invalid_rows FROM ecommerce.dwd_trade_payment_detail WHERE payment_status IS NULL OR payment_status NOT IN ('SUCCESS', 'FAILED');
