-- ============================================================
-- 对账差异诊断（只读，不改任何表）
-- ============================================================
USE lakehouse;

SELECT '=== A. 两侧合计（全区间） ===' AS section;
SELECT COUNT(*)                                                     AS windows,
       SUM(realtime_gmv)                                            AS rt_gmv,
       SUM(batch_gmv)                                               AS bt_gmv,
       SUM(realtime_order_cnt)                                      AS rt_order,
       SUM(batch_order_cnt)                                         AS bt_order,
       SUM(realtime_order_user_cnt)                                 AS rt_user,
       SUM(batch_order_user_cnt)                                    AS bt_user,
       SUM(realtime_payment_cnt)                                    AS rt_pay,
       SUM(batch_payment_cnt)                                       AS bt_pay,
       SUM(realtime_payment_amount)                                 AS rt_pay_amt,
       SUM(batch_payment_amount)                                    AS bt_pay_amt,
       SUM(realtime_payment_fail_cnt)                               AS rt_fail,
       SUM(batch_payment_fail_cnt)                                  AS bt_fail,
       SUM(realtime_refund_cnt)                                     AS rt_refund,
       SUM(batch_refund_cnt)                                        AS bt_refund,
       SUM(realtime_refund_amount)                                  AS rt_refund_amt,
       SUM(batch_refund_amount)                                     AS bt_refund_amt
FROM ads_reconcile_trade_1m;

SELECT '=== B. 按列统计不一致窗口数 ===' AS section;
SELECT SUM(CASE WHEN diff_gmv                <> 0 THEN 1 ELSE 0 END)  AS bad_gmv,
       SUM(CASE WHEN diff_order_cnt          <> 0 THEN 1 ELSE 0 END)  AS bad_order,
       SUM(CASE WHEN diff_order_user_cnt     <> 0 THEN 1 ELSE 0 END)  AS bad_user,
       SUM(CASE WHEN diff_payment_cnt        <> 0 THEN 1 ELSE 0 END)  AS bad_pay,
       SUM(CASE WHEN diff_payment_amount     <> 0 THEN 1 ELSE 0 END)  AS bad_pay_amt,
       SUM(CASE WHEN diff_payment_fail_cnt   <> 0 THEN 1 ELSE 0 END)  AS bad_fail,
       SUM(CASE WHEN diff_refund_cnt         <> 0 THEN 1 ELSE 0 END)  AS bad_refund,
       SUM(CASE WHEN diff_refund_amount      <> 0 THEN 1 ELSE 0 END)  AS bad_refund_amt,
       SUM(CASE WHEN is_match                THEN 0 ELSE 1 END)       AS bad_any
FROM ads_reconcile_trade_1m;

SELECT '=== C. |diff_gmv| 最大的 12 个窗口 ===' AS section;
SELECT window_start, realtime_gmv, batch_gmv, diff_gmv,
       realtime_order_cnt, batch_order_cnt,
       realtime_payment_cnt, batch_payment_cnt,
       realtime_payment_amount, batch_payment_amount
FROM ads_reconcile_trade_1m
ORDER BY ABS(diff_gmv) DESC
LIMIT 12;

SELECT '=== D. 实时侧整窗缺失（rt 全 0、batch 非 0） / 离线侧整窗缺失 ===' AS section;
SELECT SUM(CASE WHEN realtime_gmv = 0 AND batch_gmv <> 0 THEN 1 ELSE 0 END) AS rt_missing,
       SUM(CASE WHEN batch_gmv = 0 AND realtime_gmv <> 0 THEN 1 ELSE 0 END) AS bt_missing,
       SUM(CASE WHEN realtime_gmv = 0 AND batch_gmv = 0 THEN 1 ELSE 0 END)  AS both_zero
FROM ads_reconcile_trade_1m;

SELECT '=== E. 只有 payment_cnt 不一致的窗口（其余全对） ===' AS section;
SELECT COUNT(*) AS only_pay_windows,
       SUM(diff_payment_cnt) AS sum_diff_pay,
       MIN(window_start) AS mn, MAX(window_start) AS mx
FROM ads_reconcile_trade_1m
WHERE diff_payment_cnt <> 0
  AND diff_gmv = 0 AND diff_order_cnt = 0
  AND diff_order_user_cnt = 0 AND diff_payment_amount = 0
  AND diff_payment_fail_cnt = 0 AND diff_refund_cnt = 0
  AND diff_refund_amount = 0;

SELECT '=== F. 批层原始窗口数 / 实时窗口数 ===' AS section;
SELECT (SELECT COUNT(*) FROM ads_batch_trade_1m)  AS batch_windows,
       (SELECT SUM(batch_windows)  FROM ads_reconcile_summary) AS summ_batch,
       (SELECT SUM(realtime_windows) FROM ads_reconcile_summary) AS summ_rt,
       (SELECT MIN(window_start) FROM ads_batch_trade_1m) AS batch_min,
       (SELECT MAX(window_start) FROM ads_batch_trade_1m) AS batch_max;
