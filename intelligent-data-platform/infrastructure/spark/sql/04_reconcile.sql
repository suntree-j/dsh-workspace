-- ============================================================
-- Sprint 3 — 批流交叉对账（Spark SQL）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_3.md 第 3.3 节
-- 目标表定义：sql/hive/05_reconcile_tables.sql
--
-- 它做什么：
--   把「实时链路」（Flink 聚合 → Doris，1 分钟窗口）与
--      「离线链路」（Spark 分层 → Parquet，1 分钟窗口）
--   在同一时间区间、同一窗口粒度上逐条比对，
--   把每个窗口的双方取值与差值全部落盘。
--
-- !! 为什么实时表要显式传 jdbc 参数 !!
--   Doris 的日期时间列通过 JDBC 读出来时会带上时区换算，
--   若不固定 sessionVariables（time_zone）与时间格式，
--   window_start 会整体偏移 8 小时，对账结果全红但原因极难定位。
--   这里固定 time_zone=Asia/Shanghai 与 DATE_FORMat，
--   使读出来的 datetime 与实时表里看到的值逐字符一致。
--
-- 参数（由 reconcile_batch_realtime.py 计算后代入）：
--   ${SCOPE_START}   对账区间起点（含）
--   ${SCOPE_END}     对账区间终点（不含）
--   ${COMPARED_AT}   对账执行时间
--   ${BATCH_ID}      本次对账批次标识
--
-- 区间为什么由作业计算（而不是写死或取全量）：
--   实时链路的最后一个窗口可能还在接收迟到数据，
--   拿它去比必然"看起来不一致"，那是假警报而不是真问题。
--   作业取「两侧都已封闭」的区间（尾部留出安全边界），
--   并把区间原样写入汇总表，保证对账结论可复核。
-- ============================================================

-- ------------------------------------------------------------
-- 参数视图：从作业用 --conf 传入的值构造，避免把时间写死在 SQL 里
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_reconcile_params;
CREATE OR REPLACE TEMPORARY VIEW v_reconcile_params AS
SELECT CAST('${SCOPE_START}' AS TIMESTAMP)      AS scope_start,
       CAST('${SCOPE_END}' AS TIMESTAMP)        AS scope_end,
       CAST('${COMPARED_AT}' AS TIMESTAMP)      AS compared_at,
       '${BATCH_ID}'                            AS batch_id;

-- ------------------------------------------------------------
-- 实时侧：只取对账区间内的窗口
--
-- v_realtime_trade_src 由 reconcile_batch_realtime.py 通过 JDBC 建好
-- （Doris 的 ecommerce.ads_realtime_trade_1m）。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_realtime_scope;
CREATE OR REPLACE TEMPORARY VIEW v_realtime_scope AS
SELECT r.window_start,
       r.gmv,
       r.order_cnt,
       r.order_user_cnt,
       r.payment_cnt,
       r.payment_amount,
       r.payment_fail_cnt,
       r.refund_cnt,
       r.refund_amount
FROM v_realtime_trade_src r, v_reconcile_params p
WHERE r.window_start >= p.scope_start
  AND r.window_start <  p.scope_end;

-- ------------------------------------------------------------
-- 离线侧：同一区间
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_batch_scope;
CREATE OR REPLACE TEMPORARY VIEW v_batch_scope AS
SELECT b.window_start,
       b.gmv,
       b.order_cnt,
       b.order_user_cnt,
       b.payment_cnt,
       b.payment_amount,
       b.payment_fail_cnt,
       b.refund_cnt,
       b.refund_amount
FROM lakehouse.ads_batch_trade_1m b, v_reconcile_params p
WHERE b.window_start >= p.scope_start
  AND b.window_start <  p.scope_end;

-- ------------------------------------------------------------
-- 逐窗口对账明细
--
-- FULL OUTER JOIN 的用意：
--   实时有、离线无 → 离线漏算（diff 为一侧的完整值，一眼可见）
--   离线有、实时无 → 离线多算
--   两侧都有但不等 → 该指标 diff 非 0
--   用 INNER JOIN 会把"整行缺失"这类最严重的差异悄悄过滤掉。
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.ads_reconcile_trade_1m PARTITION (dt)
SELECT COALESCE(rt.window_start, bt.window_start) AS window_start,
       COALESCE(rt.gmv, CAST(0 AS DECIMAL(18, 2)))                        AS realtime_gmv,
       COALESCE(bt.gmv, CAST(0 AS DECIMAL(18, 2)))                        AS batch_gmv,
       COALESCE(bt.gmv, CAST(0 AS DECIMAL(18, 2)))
           - COALESCE(rt.gmv, CAST(0 AS DECIMAL(18, 2)))                   AS diff_gmv,
       CAST(COALESCE(rt.order_cnt, 0) AS BIGINT)                          AS realtime_order_cnt,
       CAST(COALESCE(bt.order_cnt, 0) AS BIGINT)                          AS batch_order_cnt,
       CAST(COALESCE(bt.order_cnt, 0) - COALESCE(rt.order_cnt, 0) AS BIGINT)         AS diff_order_cnt,
       CAST(COALESCE(rt.order_user_cnt, 0) AS BIGINT)                     AS realtime_order_user_cnt,
       CAST(COALESCE(bt.order_user_cnt, 0) AS BIGINT)                     AS batch_order_user_cnt,
       CAST(COALESCE(bt.order_user_cnt, 0) - COALESCE(rt.order_user_cnt, 0) AS BIGINT) AS diff_order_user_cnt,
       CAST(COALESCE(rt.payment_cnt, 0) AS BIGINT)                        AS realtime_payment_cnt,
       CAST(COALESCE(bt.payment_cnt, 0) AS BIGINT)                        AS batch_payment_cnt,
       CAST(COALESCE(bt.payment_cnt, 0) - COALESCE(rt.payment_cnt, 0) AS BIGINT)     AS diff_payment_cnt,
       COALESCE(rt.payment_amount, CAST(0 AS DECIMAL(18, 2)))             AS realtime_payment_amount,
       COALESCE(bt.payment_amount, CAST(0 AS DECIMAL(18, 2)))             AS batch_payment_amount,
       COALESCE(bt.payment_amount, CAST(0 AS DECIMAL(18, 2)))
           - COALESCE(rt.payment_amount, CAST(0 AS DECIMAL(18, 2)))       AS diff_payment_amount,
       CAST(COALESCE(rt.payment_fail_cnt, 0) AS BIGINT)                   AS realtime_payment_fail_cnt,
       CAST(COALESCE(bt.payment_fail_cnt, 0) AS BIGINT)                   AS batch_payment_fail_cnt,
       CAST(COALESCE(bt.payment_fail_cnt, 0) - COALESCE(rt.payment_fail_cnt, 0) AS BIGINT) AS diff_payment_fail_cnt,
       CAST(COALESCE(rt.refund_cnt, 0) AS BIGINT)                         AS realtime_refund_cnt,
       CAST(COALESCE(bt.refund_cnt, 0) AS BIGINT)                         AS batch_refund_cnt,
       CAST(COALESCE(bt.refund_cnt, 0) - COALESCE(rt.refund_cnt, 0) AS BIGINT)       AS diff_refund_cnt,
       COALESCE(rt.refund_amount, CAST(0 AS DECIMAL(18, 2)))              AS realtime_refund_amount,
       COALESCE(bt.refund_amount, CAST(0 AS DECIMAL(18, 2)))              AS batch_refund_amount,
       COALESCE(bt.refund_amount, CAST(0 AS DECIMAL(18, 2)))
           - COALESCE(rt.refund_amount, CAST(0 AS DECIMAL(18, 2)))        AS diff_refund_amount,
       (
           COALESCE(bt.gmv, CAST(0 AS DECIMAL(18, 2))) = COALESCE(rt.gmv, CAST(0 AS DECIMAL(18, 2)))
        AND COALESCE(bt.order_cnt, 0) = COALESCE(rt.order_cnt, 0)
        AND COALESCE(bt.order_user_cnt, 0) = COALESCE(rt.order_user_cnt, 0)
        AND COALESCE(bt.payment_cnt, 0) = COALESCE(rt.payment_cnt, 0)
        AND COALESCE(bt.payment_amount, CAST(0 AS DECIMAL(18, 2))) = COALESCE(rt.payment_amount, CAST(0 AS DECIMAL(18, 2)))
        AND COALESCE(bt.payment_fail_cnt, 0) = COALESCE(rt.payment_fail_cnt, 0)
        AND COALESCE(bt.refund_cnt, 0) = COALESCE(rt.refund_cnt, 0)
        AND COALESCE(bt.refund_amount, CAST(0 AS DECIMAL(18, 2))) = COALESCE(rt.refund_amount, CAST(0 AS DECIMAL(18, 2)))
       )                                                                  AS is_match,
       p.compared_at                                                      AS compared_at,
       CAST(TO_DATE(COALESCE(rt.window_start, bt.window_start)) AS STRING) AS dt
FROM v_realtime_scope rt
FULL OUTER JOIN v_batch_scope bt ON rt.window_start = bt.window_start
CROSS JOIN v_reconcile_params p;

-- ------------------------------------------------------------
-- 批次汇总
--
-- realtime_windows 的统计口径：
--   逐窗口表是 FULL OUTER JOIN 的结果，实时侧缺失的行
--   会让 realtime 三列全为 NULL（diff 列则等于离线侧的值）。
--   因此用 realtime_gmv IS NULL 判定"这一行实时侧没有"。
--   realtime_windows + 离线独有行数 = 总行数，两边都能被暴露出来。
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.ads_reconcile_summary PARTITION (dt)
SELECT p.batch_id                                                         AS batch_id,
       p.compared_at                                                      AS compared_at,
       p.scope_start                                                      AS scope_start,
       p.scope_end                                                        AS scope_end,
       CAST(COUNT(*) - SUM(CASE WHEN realtime_gmv IS NULL THEN 1 ELSE 0 END) AS BIGINT) AS realtime_windows,
       CAST(COUNT(*) - SUM(CASE WHEN batch_gmv IS NULL THEN 1 ELSE 0 END) AS BIGINT)    AS batch_windows,
       CAST(SUM(CASE WHEN is_match THEN 1 ELSE 0 END) AS BIGINT)          AS matched_windows,
       CAST(SUM(CASE WHEN is_match THEN 0 ELSE 1 END) AS BIGINT)          AS mismatched_windows,
       MIN(CASE WHEN is_match THEN NULL ELSE window_start END)            AS first_mismatch_at,
       SUM(realtime_gmv)                                                  AS realtime_total_gmv,
       SUM(batch_gmv)                                                     AS batch_total_gmv,
       (SUM(CASE WHEN is_match THEN 0 ELSE 1 END) = 0)                    AS is_pass,
       CAST(TO_DATE(p.compared_at) AS STRING)                             AS dt
FROM lakehouse.ads_reconcile_trade_1m, v_reconcile_params p
GROUP BY p.batch_id, p.compared_at, p.scope_start, p.scope_end;
