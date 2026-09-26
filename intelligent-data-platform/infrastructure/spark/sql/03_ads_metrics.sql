-- ============================================================
-- Sprint 3 — DWD → ADS 指标作业（Spark SQL）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_3.md 第 3.3 节
-- 口径：sql/metadata/metrics.md（唯一权威定义，本文件不得另立口径）
-- 目标表定义：sql/hive/04_ads_tables.sql
--
-- !! 本文件的核心要求：与实时链路的 Flink 作业逐公式同构 !!
--   实时侧公式见 infrastructure/flink/sql/05_metric_jobs.sql。
--   离线侧每一个指标都必须是实时侧那一条公式的**逐字翻译**，
--   不允许"顺手优化"（例如把 SUM(CASE WHEN ...) 换成 COUNT(*) FILTER），
--   因为任何改写都可能改变空值与取整行为，对账就会失败。
--
-- 三个必须与实时侧保持一致的实现细节：
--   1. 窗口 = TUMBLE 1 分钟 → 用 date_trunc('minute', event_time) 表达，左闭右开；
--   2. 可加指标无事件时为 0（COALESCE(..., 0)），不是 NULL；
--   3. 比率指标分母为 0 时为 NULL（用 CASE WHEN 分母 > 0 实现，不用 NULLIF 之外的兜底）。
-- ============================================================

-- ------------------------------------------------------------
-- 1. ADS：离线交易总览（1 分钟粒度）
--
-- 实现思路与 Flink 作业一致：
--   三条独立事件流 → 各自按分钟聚合 → FULL OUTER JOIN（按窗口对齐）
--   → 外层用 CASE WHEN 算比率。
--
-- 为什么用 FULL OUTER JOIN 而不是 UNION ALL 透视（Flink 的做法）：
--   Flink 侧三条流是**无界的**，只能先 UNION 成 (指标名, 值) 再透视；
--   离线侧是有界批处理，按窗口 FULL OUTER JOIN 更直观，
--   且能保证"某窗口只有支付没有订单"时仍然输出一行。
--   两者的**输出完全等价**：可加指标补 0、比率留 NULL。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_win_order;
CREATE OR REPLACE TEMPORARY VIEW v_win_order AS
-- 注意：这里的 COUNT(DISTINCT user_id) 是**故意**保留的近似去重。
--   实时链路用的是 Flink 的 COUNT(DISTINCT user_id)（同样基于
--   有界去重/近似基数），而 order_user_cnt 是**参与批流对账的指标**。
--   如果离线侧换成精确去重，两边就会因为"算法不同"而对不上，
--   对账失败的原因也会变得难以判断。
--   因此：**指标列跟随实时侧的实现语义**；
--   而"用来做相等断言的去重计数"必须用精确实现
--   （见 infrastructure/spark/jobs/_common.py 的 exact_distinct）。
SELECT DATE_TRUNC('minute', event_time) AS window_start,
       COUNT(*)                         AS order_cnt,
       COUNT(DISTINCT user_id)          AS order_user_cnt,
       SUM(amount)                      AS gmv
FROM lakehouse.dwd_trade_order_detail
GROUP BY DATE_TRUNC('minute', event_time);

DROP VIEW IF EXISTS v_win_payment;
CREATE OR REPLACE TEMPORARY VIEW v_win_payment AS
SELECT DATE_TRUNC('minute', event_time) AS window_start,
       SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS payment_cnt,
       SUM(CASE WHEN payment_status = 'SUCCESS' THEN amount ELSE CAST(0 AS DECIMAL(18, 2)) END) AS payment_amount,
       SUM(CASE WHEN payment_status = 'FAILED' THEN 1 ELSE 0 END)  AS payment_fail_cnt
FROM lakehouse.dwd_trade_payment_detail
GROUP BY DATE_TRUNC('minute', event_time);

DROP VIEW IF EXISTS v_win_refund;
CREATE OR REPLACE TEMPORARY VIEW v_win_refund AS
SELECT DATE_TRUNC('minute', event_time) AS window_start,
       COUNT(*)                         AS refund_cnt,
       SUM(refund_amount)               AS refund_amount
FROM lakehouse.dwd_trade_refund_detail
GROUP BY DATE_TRUNC('minute', event_time);

INSERT OVERWRITE TABLE lakehouse.ads_batch_trade_1m PARTITION (dt)
SELECT COALESCE(o.window_start, p.window_start, r.window_start) AS window_start,
       COALESCE(o.window_start, p.window_start, r.window_start) + INTERVAL 1 MINUTE AS window_end,
       COALESCE(o.gmv, CAST(0 AS DECIMAL(18, 2)))                          AS gmv,
       CAST(COALESCE(o.order_cnt, 0) AS BIGINT)                            AS order_cnt,
       CAST(COALESCE(o.order_user_cnt, 0) AS BIGINT)                       AS order_user_cnt,
       -- 客单价：与实时侧一致，分母为 0 时 NULL
       CASE WHEN COALESCE(o.order_cnt, 0) > 0
            THEN CAST(o.gmv / o.order_cnt AS DECIMAL(18, 2))
       END                                                                 AS avg_order_amount,
       CAST(COALESCE(p.payment_cnt, 0) AS BIGINT)                          AS payment_cnt,
       COALESCE(p.payment_amount, CAST(0 AS DECIMAL(18, 2)))               AS payment_amount,
       CAST(COALESCE(p.payment_fail_cnt, 0) AS BIGINT)                     AS payment_fail_cnt,
       CASE WHEN COALESCE(p.payment_cnt, 0) + COALESCE(p.payment_fail_cnt, 0) > 0
            THEN CAST(p.payment_cnt / (p.payment_cnt + p.payment_fail_cnt) AS DECIMAL(10, 4))
       END                                                                 AS payment_success_rate,
       CAST(COALESCE(r.refund_cnt, 0) AS BIGINT)                           AS refund_cnt,
       COALESCE(r.refund_amount, CAST(0 AS DECIMAL(18, 2)))                AS refund_amount,
       CASE WHEN COALESCE(p.payment_amount, 0) > 0
            THEN CAST(r.refund_amount / p.payment_amount AS DECIMAL(10, 4))
       END                                                                 AS refund_rate,
       CAST(TO_DATE(COALESCE(o.window_start, p.window_start, r.window_start)) AS STRING) AS dt
FROM v_win_order   o
FULL OUTER JOIN v_win_payment p ON o.window_start = p.window_start
FULL OUTER JOIN v_win_refund  r ON COALESCE(o.window_start, p.window_start) = r.window_start;

-- ------------------------------------------------------------
-- 2. ADS：离线交易总览（1 天）—— 分钟结果上卷 + DWD 精确去重
--
-- 为什么从 1m 上卷而不是直接从 DWD 按天聚合：
--   两条路径算出的"当日 GMV"必须相等，否则看板与对账结果会自相矛盾。
--   统一从 1m 上卷，保证「天 = 该天所有分钟之和」这条关系恒成立。
--
-- !! 为什么 order_user_cnt 不能 SUM !!
--   去重计数**不可加**：同一用户可能在多个分钟窗口下单，
--   SUM(order_user_cnt) 会把他重复计数。这里回到 DWD 按天精确去重。
--   同理，比率指标也从"上卷后的可加量"重算，而不是对分钟比率求平均
--   （对率求平均是典型的口径错误）。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_trade_day_sum;
CREATE OR REPLACE TEMPORARY VIEW v_trade_day_sum AS
SELECT dt,
       SUM(gmv)                AS gmv,
       SUM(order_cnt)          AS order_cnt,
       SUM(payment_cnt)        AS payment_cnt,
       SUM(payment_amount)     AS payment_amount,
       SUM(payment_fail_cnt)   AS payment_fail_cnt,
       SUM(refund_cnt)         AS refund_cnt,
       SUM(refund_amount)      AS refund_amount
FROM lakehouse.ads_batch_trade_1m
GROUP BY dt;

DROP VIEW IF EXISTS v_trade_day_user;
CREATE OR REPLACE TEMPORARY VIEW v_trade_day_user AS
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       COUNT(DISTINCT user_id)             AS order_user_cnt
FROM lakehouse.dwd_trade_order_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING);

INSERT OVERWRITE TABLE lakehouse.ads_batch_trade_1d PARTITION (part_dt)
SELECT s.dt                                                    AS dt,
       s.gmv                                                   AS gmv,
       CAST(s.order_cnt AS BIGINT)                             AS order_cnt,
       CAST(COALESCE(u.order_user_cnt, 0) AS BIGINT)           AS order_user_cnt,
       CASE WHEN s.order_cnt > 0
            THEN CAST(s.gmv / s.order_cnt AS DECIMAL(18, 2))
       END                                                     AS avg_order_amount,
       CAST(s.payment_cnt AS BIGINT)                           AS payment_cnt,
       s.payment_amount                                        AS payment_amount,
       CAST(s.payment_fail_cnt AS BIGINT)                      AS payment_fail_cnt,
       CASE WHEN s.payment_cnt + s.payment_fail_cnt > 0
            THEN CAST(s.payment_cnt / (s.payment_cnt + s.payment_fail_cnt) AS DECIMAL(10, 4))
       END                                                     AS payment_success_rate,
       CAST(s.refund_cnt AS BIGINT)                            AS refund_cnt,
       s.refund_amount                                         AS refund_amount,
       CASE WHEN s.payment_amount > 0
            THEN CAST(s.refund_amount / s.payment_amount AS DECIMAL(10, 4))
       END                                                     AS refund_rate,
       s.dt                                                    AS part_dt
FROM v_trade_day_sum s
LEFT JOIN v_trade_day_user u ON s.dt = u.dt;

-- ------------------------------------------------------------
-- 3. ADS：离线类目销售（1 分钟，与实时类目表同形）
--
-- 实时侧的类目来自 Kafka 事件里的冗余字段 category_name
-- （见 metrics.md 第 5 节：类目不做 lookup join）。
-- 离线侧取 DWD 补全后的 category_name —— 两者同源（同一份 product 主数据），
-- 这正是"批流同口径"成立的前提。
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.ads_batch_category_1m PARTITION (dt)
SELECT DATE_TRUNC('minute', event_time)                       AS window_start,
       COALESCE(category_name, 'UNKNOWN')                     AS category_name,
       DATE_TRUNC('minute', event_time) + INTERVAL 1 MINUTE    AS window_end,
       CAST(COUNT(*) AS BIGINT)                               AS order_cnt,
       SUM(amount)                                            AS gmv,
       CAST(SUM(quantity) AS BIGINT)                          AS total_quantity,
       CAST(SUM(amount) / COUNT(*) AS DECIMAL(18, 2))         AS avg_order_amount,
       CAST(TO_DATE(event_time) AS STRING)                    AS dt
FROM lakehouse.dwd_trade_order_detail
GROUP BY DATE_TRUNC('minute', event_time),
         COALESCE(category_name, 'UNKNOWN'),
         CAST(TO_DATE(event_time) AS STRING);

-- ------------------------------------------------------------
-- 4. ADS：离线类目销售（1 天）
-- 同样：去重计数与比率都从明细重算，不对分钟结果做 SUM / AVG
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_category_day_sum;
CREATE OR REPLACE TEMPORARY VIEW v_category_day_sum AS
SELECT dt,
       category_name,
       SUM(order_cnt)      AS order_cnt,
       SUM(gmv)            AS gmv,
       SUM(total_quantity) AS total_quantity
FROM lakehouse.ads_batch_category_1m
GROUP BY dt, category_name;

DROP VIEW IF EXISTS v_category_day_user;
CREATE OR REPLACE TEMPORARY VIEW v_category_day_user AS
SELECT CAST(TO_DATE(event_time) AS STRING)         AS dt,
       COALESCE(category_name, 'UNKNOWN')          AS category_name,
       COUNT(DISTINCT user_id)                     AS order_user_cnt
FROM lakehouse.dwd_trade_order_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING),
         COALESCE(category_name, 'UNKNOWN');

INSERT OVERWRITE TABLE lakehouse.ads_batch_category_1d PARTITION (part_dt)
SELECT s.dt                                          AS dt,
       s.category_name                               AS category_name,
       CAST(s.order_cnt AS BIGINT)                   AS order_cnt,
       CAST(COALESCE(u.order_user_cnt, 0) AS BIGINT) AS order_user_cnt,
       s.gmv                                         AS gmv,
       CAST(s.total_quantity AS BIGINT)              AS total_quantity,
       CASE WHEN s.order_cnt > 0
            THEN CAST(s.gmv / s.order_cnt AS DECIMAL(18, 2))
       END                                           AS avg_order_amount,
       s.dt                                          AS part_dt
FROM v_category_day_sum s
LEFT JOIN v_category_day_user u
       ON s.dt = u.dt AND s.category_name = u.category_name;
