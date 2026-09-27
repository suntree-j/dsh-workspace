-- ============================================================
-- Sprint 5 — 流量域批流交叉对账（Spark SQL）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_5.md 第 3.4 / 3.5 节
-- 目标表定义：sql/hive/05_reconcile_tables.sql
--
-- 它做什么：
--   把「实时链路」（Flink 1 分钟 TUMBLE → Doris）
--     与「离线链路」（Spark 分层 → 湖仓 1 分钟窗口）
--   在**同一时间区间、同一窗口粒度**上逐条比对，
--   把每个窗口的双方取值与差值全部落盘。
--
-- 与交易域对账（04_reconcile.sql）的两处实质差别
-- ----------------------------------------------
--
-- **1. 判据列不同：这里必比的是「去重指标 + 可加指标」**
--   交易域只比可加量（gmv / 笔数 / 金额），order_user_cnt 的近似去重
--   两边同为近似实现，仍参与比对。
--   流量域的核心指标恰恰是 uv（去重）与 pv（可加）：
--     - uv  两侧都用 COUNT(DISTINCT user_id)（实时 Flink 的近似实现 +
--           离线 Spark 的近似实现），**逐窗口比对成立**；
--     - 但窗口之间不可加 —— 所以本文件只做逐窗口比对，
--          绝不做"把 uv 上卷后比总数"这种事。
--     - pv / 6 个行为计数可加，两侧都可上卷，因此既逐窗口比、也比合计。
--
-- **2. 比率列只留证，不参与 is_match**
--   原因要写清楚，否则看起来像"放水"：
--     click_rate / cart_rate / buy_rate 不是独立指标，它们是
--     分子分母的**派生量**。上面 8 个列（uv/pv/6 个计数）**逐窗口相等**时，
--     比率在数学上必然相等 —— 它们不提供任何额外的正确性信息。
--     而它们又最容易产生**假差异**：两侧的除法实现不同
--     （实时是 Flink 的 DECIMAL 除法，离线是 Spark 的 DECIMAL 除法），
--     中间精度与舍入规则不完全一致，末位可能差 1 个 ULP。
--     让它参与判据，等价于用"实现细节"当"数据正确性"的判据。
--   ★ 因此：比率列全部落盘（diff_*_rate），**并且作业会显式统计
--     "比率列也不一致的窗口数"并打印出来** —— 等于零才算真正全部一致。
--     如果那个数不为 0，说明两侧除法实现有差异，必须如实记录，
--     而不是把它藏起来。判据不放宽，但差异也不掩盖。
--
-- 参数（由 reconcile_traffic_batch_realtime.py 计算后代入）：
--   ${SCOPE_START}   对账区间起点（含）
--   ${SCOPE_END}     对账区间终点（不含）
--   ${COMPARED_AT}   对账执行时间
--   ${BATCH_ID}      本次对账批次标识
-- ============================================================

-- ------------------------------------------------------------
-- 参数视图
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_reconcile_params;
CREATE OR REPLACE TEMPORARY VIEW v_reconcile_params AS
SELECT CAST('${SCOPE_START}' AS TIMESTAMP)      AS scope_start,
       CAST('${SCOPE_END}' AS TIMESTAMP)        AS scope_end,
       CAST('${COMPARED_AT}' AS TIMESTAMP)      AS compared_at,
       '${BATCH_ID}'                            AS batch_id;

-- ------------------------------------------------------------
-- 实时侧：只取对账区间内的窗口
-- v_realtime_traffic_src 由作业通过 JDBC 建好（Doris ecommerce.ads_realtime_traffic_1m）
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_realtime_traffic_scope;
CREATE OR REPLACE TEMPORARY VIEW v_realtime_traffic_scope AS
SELECT r.window_start,
       r.uv,
       r.pv,
       r.view_cnt,
       r.click_cnt,
       r.cart_cnt,
       r.favorite_cnt,
       r.buy_cnt,
       r.click_rate,
       r.cart_rate,
       r.buy_rate
FROM v_realtime_traffic_src r, v_reconcile_params p
WHERE r.window_start >= p.scope_start
  AND r.window_start <  p.scope_end;

-- ------------------------------------------------------------
-- 离线侧：同一区间
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_batch_traffic_scope;
CREATE OR REPLACE TEMPORARY VIEW v_batch_traffic_scope AS
SELECT b.window_start,
       b.uv,
       b.pv,
       b.view_cnt,
       b.click_cnt,
       b.cart_cnt,
       b.favorite_cnt,
       b.buy_cnt,
       b.click_rate,
       b.cart_rate,
       b.buy_rate
FROM lakehouse.ads_traffic_1m b, v_reconcile_params p
WHERE b.window_start >= p.scope_start
  AND b.window_start <  p.scope_end;

-- ------------------------------------------------------------
-- 逐窗口对账明细
--
-- FULL OUTER JOIN 的用意（与交易域一致）：
--   实时有、离线无 → 离线漏算；离线有、实时无 → 离线多算。
--   用 INNER JOIN 会把"整行缺失"这类最严重的差异悄悄过滤掉。
--   流量域两侧都可能有**完全没事件的空档**，缺失窗口比交易域更常见，
--   所以这里更不能省。
--
-- 缺失侧的取值约定：
--   计数类缺失记 0（与实时侧"无事件=0"的约定一致）；
--   比率类缺失记 NULL（与实时侧"分母为 0=NULL"的约定一致）。
--   这样 is_match 的比较语义与两侧自身的数据语义是同一套，而不是另立一套。
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.ads_reconcile_traffic_1m PARTITION (dt)
SELECT COALESCE(rt.window_start, bt.window_start) AS window_start,
       CAST(rt.uv AS BIGINT)                      AS realtime_uv,
       CAST(bt.uv AS BIGINT)                      AS batch_uv,
       CAST(COALESCE(bt.uv, 0) - COALESCE(rt.uv, 0) AS BIGINT)           AS diff_uv,
       CAST(COALESCE(rt.pv, 0) AS BIGINT)                                 AS realtime_pv,
       CAST(COALESCE(bt.pv, 0) AS BIGINT)                                 AS batch_pv,
       CAST(COALESCE(bt.pv, 0) - COALESCE(rt.pv, 0) AS BIGINT)            AS diff_pv,
       CAST(COALESCE(rt.view_cnt, 0) AS BIGINT)                           AS realtime_view_cnt,
       CAST(COALESCE(bt.view_cnt, 0) AS BIGINT)                           AS batch_view_cnt,
       CAST(COALESCE(bt.view_cnt, 0) - COALESCE(rt.view_cnt, 0) AS BIGINT) AS diff_view_cnt,
       CAST(COALESCE(rt.click_cnt, 0) AS BIGINT)                          AS realtime_click_cnt,
       CAST(COALESCE(bt.click_cnt, 0) AS BIGINT)                          AS batch_click_cnt,
       CAST(COALESCE(bt.click_cnt, 0) - COALESCE(rt.click_cnt, 0) AS BIGINT) AS diff_click_cnt,
       CAST(COALESCE(rt.cart_cnt, 0) AS BIGINT)                           AS realtime_cart_cnt,
       CAST(COALESCE(bt.cart_cnt, 0) AS BIGINT)                           AS batch_cart_cnt,
       CAST(COALESCE(bt.cart_cnt, 0) - COALESCE(rt.cart_cnt, 0) AS BIGINT) AS diff_cart_cnt,
       CAST(COALESCE(rt.favorite_cnt, 0) AS BIGINT)                       AS realtime_favorite_cnt,
       CAST(COALESCE(bt.favorite_cnt, 0) AS BIGINT)                       AS batch_favorite_cnt,
       CAST(COALESCE(bt.favorite_cnt, 0) - COALESCE(rt.favorite_cnt, 0) AS BIGINT) AS diff_favorite_cnt,
       CAST(COALESCE(rt.buy_cnt, 0) AS BIGINT)                            AS realtime_buy_cnt,
       CAST(COALESCE(bt.buy_cnt, 0) AS BIGINT)                            AS batch_buy_cnt,
       CAST(COALESCE(bt.buy_cnt, 0) - COALESCE(rt.buy_cnt, 0) AS BIGINT)  AS diff_buy_cnt,
       CAST(rt.click_rate AS DECIMAL(10, 4))                              AS realtime_click_rate,
       CAST(bt.click_rate AS DECIMAL(10, 4))                              AS batch_click_rate,
       CAST(bt.click_rate AS DECIMAL(10, 4)) - CAST(rt.click_rate AS DECIMAL(10, 4)) AS diff_click_rate,
       CAST(rt.cart_rate AS DECIMAL(10, 4))                               AS realtime_cart_rate,
       CAST(bt.cart_rate AS DECIMAL(10, 4))                               AS batch_cart_rate,
       CAST(bt.cart_rate AS DECIMAL(10, 4)) - CAST(rt.cart_rate AS DECIMAL(10, 4)) AS diff_cart_rate,
       CAST(rt.buy_rate AS DECIMAL(10, 4))                                AS realtime_buy_rate,
       CAST(bt.buy_rate AS DECIMAL(10, 4))                                AS batch_buy_rate,
       CAST(bt.buy_rate AS DECIMAL(10, 4)) - CAST(rt.buy_rate AS DECIMAL(10, 4)) AS diff_buy_rate,
       (
           COALESCE(bt.uv, 0)           = COALESCE(rt.uv, 0)
        AND COALESCE(bt.pv, 0)           = COALESCE(rt.pv, 0)
        AND COALESCE(bt.view_cnt, 0)     = COALESCE(rt.view_cnt, 0)
        AND COALESCE(bt.click_cnt, 0)    = COALESCE(rt.click_cnt, 0)
        AND COALESCE(bt.cart_cnt, 0)     = COALESCE(rt.cart_cnt, 0)
        AND COALESCE(bt.favorite_cnt, 0) = COALESCE(rt.favorite_cnt, 0)
        AND COALESCE(bt.buy_cnt, 0)      = COALESCE(rt.buy_cnt, 0)
       )                                                                  AS is_match,
       -- 逐窗口取证：某一侧自己的比率列与"由自己的计数重算"的结果是否矛盾。
       -- 这是把"比率列归谁的问题"从推断变成**可查的行**。
       -- 判据用 metrics.md 第 3 节的公式，不另立口径：
       --   分母为 0 → 该比率应为 NULL；分母 > 0 → 应等于 计数/分母 的 DECIMAL(10,4)。
       (
              (COALESCE(rt.view_cnt, 0) > 0 AND
               COALESCE(rt.click_rate, CAST(-1 AS DECIMAL(10, 4)))
                 <> CAST(COALESCE(rt.click_cnt, 0) / rt.view_cnt AS DECIMAL(10, 4)))
           OR (COALESCE(rt.view_cnt, 0) = 0 AND rt.click_rate IS NOT NULL)
           OR (COALESCE(rt.click_cnt, 0) > 0 AND
               COALESCE(rt.cart_rate, CAST(-1 AS DECIMAL(10, 4)))
                 <> CAST(COALESCE(rt.cart_cnt, 0) / rt.click_cnt AS DECIMAL(10, 4)))
           OR (COALESCE(rt.click_cnt, 0) = 0 AND rt.cart_rate IS NOT NULL)
           OR (COALESCE(rt.cart_cnt, 0) > 0 AND
               COALESCE(rt.buy_rate, CAST(-1 AS DECIMAL(10, 4)))
                 <> CAST(COALESCE(rt.buy_cnt, 0) / rt.cart_cnt AS DECIMAL(10, 4)))
           OR (COALESCE(rt.cart_cnt, 0) = 0 AND rt.buy_rate IS NOT NULL)
       )                                                                  AS realtime_rate_anomaly,
       (
              (COALESCE(bt.view_cnt, 0) > 0 AND
               COALESCE(bt.click_rate, CAST(-1 AS DECIMAL(10, 4)))
                 <> CAST(COALESCE(bt.click_cnt, 0) / bt.view_cnt AS DECIMAL(10, 4)))
           OR (COALESCE(bt.view_cnt, 0) = 0 AND bt.click_rate IS NOT NULL)
           OR (COALESCE(bt.click_cnt, 0) > 0 AND
               COALESCE(bt.cart_rate, CAST(-1 AS DECIMAL(10, 4)))
                 <> CAST(COALESCE(bt.cart_cnt, 0) / bt.click_cnt AS DECIMAL(10, 4)))
           OR (COALESCE(bt.click_cnt, 0) = 0 AND bt.cart_rate IS NOT NULL)
           OR (COALESCE(bt.cart_cnt, 0) > 0 AND
               COALESCE(bt.buy_rate, CAST(-1 AS DECIMAL(10, 4)))
                 <> CAST(COALESCE(bt.buy_cnt, 0) / bt.cart_cnt AS DECIMAL(10, 4)))
           OR (COALESCE(bt.cart_cnt, 0) = 0 AND bt.buy_rate IS NOT NULL)
       )                                                                  AS batch_rate_anomaly,
       p.compared_at                                                      AS compared_at,
       CAST(TO_DATE(COALESCE(rt.window_start, bt.window_start)) AS STRING) AS dt
FROM v_realtime_traffic_scope rt
FULL OUTER JOIN v_batch_traffic_scope bt ON rt.window_start = bt.window_start
CROSS JOIN v_reconcile_params p;

-- ------------------------------------------------------------
-- 批次汇总
--
-- realtime_only_windows / batch_only_windows 用 realtime_uv IS NULL /
-- batch_uv IS NULL 判定"这一行是单边有的" —— 与交易域用 gmv IS NULL 同理。
-- 为什么 uv 可以当存在性探针：本表的窗口都是"至少一侧有事件"的窗口，
-- 而有事件就必有 uv >= 1；uv 为 NULL 只可能是"这一侧根本没有这一行"。
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.ads_reconcile_traffic_summary PARTITION (dt)
SELECT p.batch_id                                                          AS batch_id,
       p.compared_at                                                       AS compared_at,
       p.scope_start                                                       AS scope_start,
       p.scope_end                                                         AS scope_end,
       CAST(COUNT(*) - SUM(CASE WHEN realtime_uv IS NULL THEN 1 ELSE 0 END) AS BIGINT) AS realtime_windows,
       CAST(COUNT(*) - SUM(CASE WHEN batch_uv    IS NULL THEN 1 ELSE 0 END) AS BIGINT) AS batch_windows,
       CAST(SUM(CASE WHEN realtime_uv IS NOT NULL AND batch_uv IS NULL THEN 1 ELSE 0 END) AS BIGINT) AS realtime_only_windows,
       CAST(SUM(CASE WHEN batch_uv IS NOT NULL AND realtime_uv IS NULL THEN 1 ELSE 0 END) AS BIGINT) AS batch_only_windows,
       CAST(SUM(CASE WHEN is_match THEN 1 ELSE 0 END) AS BIGINT)           AS matched_windows,
       CAST(SUM(CASE WHEN is_match THEN 0 ELSE 1 END) AS BIGINT)           AS mismatched_windows,
       MIN(CASE WHEN is_match THEN NULL ELSE window_start END)             AS first_mismatch_at,
       CAST(MIN(realtime_uv) AS BIGINT)                                    AS realtime_min_uv,
       CAST(MAX(realtime_uv) AS BIGINT)                                    AS realtime_max_uv,
       CAST(MIN(batch_uv) AS BIGINT)                                       AS batch_min_uv,
       CAST(MAX(batch_uv) AS BIGINT)                                       AS batch_max_uv,
       CAST(SUM(realtime_pv) AS BIGINT)                                    AS realtime_total_pv,
       CAST(SUM(batch_pv) AS BIGINT)                                       AS batch_total_pv,
       -- !! 比率列的独立取证：这两列是本次对账最重要的"新发现" !!
       --   含义：某一侧**存下来的**比率列，与它**自己的**计数列按
       --   metrics.md 第 3 节公式重算的结果不一致的窗口数。
       --   （逐窗口的判定落在 ads_reconcile_traffic_1m 的
       --     realtime_rate_anomaly / batch_rate_anomaly 两列上，这里只是汇总。）
       --
       --   为什么必须落在表里而不是只打印：
       --     它是一个**真实数据缺陷**的计数（实测当前实时侧为 1、离线侧为 0），
       --     对账结论必须能带着这个数字被复核，而不是靠翻日志。
       --   为什么它能定位责任方：
       --     同一个判据、同一个公式，在离线侧为 0 而在实时侧为 1，
       --     说明差异不在"两侧算法不同"，而在实时侧那一行本身自相矛盾。
       CAST(SUM(CASE WHEN realtime_rate_anomaly THEN 1 ELSE 0 END) AS BIGINT) AS realtime_rate_anomaly_windows,
       CAST(SUM(CASE WHEN batch_rate_anomaly    THEN 1 ELSE 0 END) AS BIGINT) AS batch_rate_anomaly_windows,
       (SUM(CASE WHEN is_match THEN 0 ELSE 1 END) = 0)                     AS is_pass,
       CAST(TO_DATE(p.compared_at) AS STRING)                              AS dt
FROM lakehouse.ads_reconcile_traffic_1m, v_reconcile_params p
GROUP BY p.batch_id, p.compared_at, p.scope_start, p.scope_end;
