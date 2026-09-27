-- 交易域主键：NULL 主键数 + 主键去重后"丢失行数"
--
-- !! 为什么不写成 GROUP BY ... HAVING COUNT(*) > 1 !!
--   Doris 的 UNIQUE KEY + merge-on-write 在存储层就已经按主键合并了，
--   GROUP BY 查重永远返回 0 —— 那是一条**恒真**的校验（等于没查）。
--   真正会出问题的是"主键为 NULL"与"声明是主键但并不唯一"这两件事，
--   它们用 NULL 计数与 COUNT(*) - COUNT(DISTINCT key) 才能真的测出来。
-- 返回 0 才算通过；正常值实测为 0
SELECT (SELECT COUNT(*) FROM ecommerce.dwd_trade_order_detail WHERE order_id IS NULL) + (SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM ecommerce.dwd_trade_order_detail) + (SELECT COUNT(*) FROM ecommerce.dwd_trade_payment_detail WHERE payment_id IS NULL) + (SELECT COUNT(*) - COUNT(DISTINCT payment_id) FROM ecommerce.dwd_trade_payment_detail) + (SELECT COUNT(*) FROM ecommerce.dwd_trade_refund_detail WHERE refund_id IS NULL) + (SELECT COUNT(*) - COUNT(DISTINCT refund_id) FROM ecommerce.dwd_trade_refund_detail) AS dup_or_null_pk;
