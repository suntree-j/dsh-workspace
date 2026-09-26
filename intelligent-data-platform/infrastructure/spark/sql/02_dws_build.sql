-- ============================================================
-- Sprint 3 — DWD → DWS 轻度聚合作业（Spark SQL）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_3.md 第 3.1 节
-- 目标表定义：sql/hive/03_dws_tables.sql
--
-- 本层只出「可加指标」：笔数 / 金额 / 件数。
-- 比率（客单价 / 成功率 / 退款率）一律留到 ADS 层按 metrics.md 的公式计算，
-- 避免同一个比率在两个层里各写一份公式。
--
-- 幂等：按 part_dt 动态分区覆盖，重跑同一批数据结果不变。
-- ============================================================

-- ------------------------------------------------------------
-- 1. DWS：交易总览 1 天
--
-- 实现方式：先取"有事件的日期骨架"，三条流各自按天聚合后再 LEFT JOIN 回骨架。
--
-- !! 为什么不直接把三条流 JOIN 起来 !!
--   订单 / 支付 / 退款的事件时间天然错位：某天可能只有支付没有下单
--   （订单在前一天创建、次日才支付），也可能只有退款。
--   INNER JOIN 会丢行；两两 LEFT JOIN 又会因为"选哪条流当主表"而漏日期。
--   先建日期骨架再 LEFT JOIN + COALESCE(..., 0)，
--   才能正确表达「该天这张表确实没有事件 → 该指标为 0」
--   （与 sql/metadata/metrics.md 第 2.1 节的空值约定一致）。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_trade_spine;
CREATE OR REPLACE TEMPORARY VIEW v_trade_spine AS
SELECT DISTINCT dt FROM (
    SELECT CAST(TO_DATE(event_time) AS STRING) AS dt FROM lakehouse.dwd_trade_order_detail
    UNION
    SELECT CAST(TO_DATE(event_time) AS STRING) AS dt FROM lakehouse.dwd_trade_payment_detail
    UNION
    SELECT CAST(TO_DATE(event_time) AS STRING) AS dt FROM lakehouse.dwd_trade_refund_detail
) t
WHERE dt IS NOT NULL;

DROP VIEW IF EXISTS v_order_1d;
CREATE OR REPLACE TEMPORARY VIEW v_order_1d AS
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       COUNT(*)                            AS order_cnt,
       COUNT(DISTINCT user_id)             AS order_user_cnt,
       SUM(amount)                         AS gmv,
       SUM(quantity)                       AS total_quantity
FROM lakehouse.dwd_trade_order_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING);

DROP VIEW IF EXISTS v_payment_1d;
CREATE OR REPLACE TEMPORARY VIEW v_payment_1d AS
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       SUM(CASE WHEN payment_status = 'SUCCESS' THEN 1 ELSE 0 END) AS payment_cnt,
       SUM(CASE WHEN payment_status = 'SUCCESS' THEN amount ELSE CAST(0 AS DECIMAL(18, 2)) END) AS payment_amount,
       SUM(CASE WHEN payment_status <> 'SUCCESS' THEN 1 ELSE 0 END) AS payment_fail_cnt
FROM lakehouse.dwd_trade_payment_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING);

DROP VIEW IF EXISTS v_refund_1d;
CREATE OR REPLACE TEMPORARY VIEW v_refund_1d AS
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       COUNT(*)           AS refund_cnt,
       SUM(refund_amount) AS refund_amount
FROM lakehouse.dwd_trade_refund_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING);

INSERT OVERWRITE TABLE lakehouse.dws_trade_overview_1d PARTITION (part_dt)
SELECT s.dt                                           AS dt,
       COALESCE(o.order_cnt, 0)                       AS order_cnt,
       COALESCE(o.order_user_cnt, 0)                  AS order_user_cnt,
       COALESCE(o.gmv, CAST(0 AS DECIMAL(18, 2)))     AS gmv,
       COALESCE(o.total_quantity, 0)                  AS total_quantity,
       COALESCE(p.payment_cnt, 0)                     AS payment_cnt,
       COALESCE(p.payment_amount, CAST(0 AS DECIMAL(18, 2))) AS payment_amount,
       COALESCE(p.payment_fail_cnt, 0)                AS payment_fail_cnt,
       COALESCE(r.refund_cnt, 0)                      AS refund_cnt,
       COALESCE(r.refund_amount, CAST(0 AS DECIMAL(18, 2))) AS refund_amount,
       s.dt                                           AS part_dt
FROM v_trade_spine s
LEFT JOIN v_order_1d   o ON s.dt = o.dt
LEFT JOIN v_payment_1d p ON s.dt = p.dt
LEFT JOIN v_refund_1d  r ON s.dt = r.dt;

-- ------------------------------------------------------------
-- 2. DWS：类目销售 1 天
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.dws_trade_category_1d PARTITION (part_dt)
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       COALESCE(category_name, 'UNKNOWN')  AS category_name,
       COUNT(*)                            AS order_cnt,
       COUNT(DISTINCT user_id)             AS order_user_cnt,
       SUM(amount)                         AS gmv,
       SUM(quantity)                       AS total_quantity,
       CAST(TO_DATE(event_time) AS STRING) AS part_dt
FROM lakehouse.dwd_trade_order_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING),
         COALESCE(category_name, 'UNKNOWN');

-- ------------------------------------------------------------
-- 3. DWS：用户交易 1 天
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.dws_trade_user_1d PARTITION (part_dt)
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       user_id,
       MAX(user_level)                     AS user_level,
       MAX(province)                       AS province,
       COUNT(*)                            AS order_cnt,
       SUM(amount)                         AS gmv,
       SUM(quantity)                       AS total_quantity,
       CAST(TO_DATE(event_time) AS STRING) AS part_dt
FROM lakehouse.dwd_trade_order_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING), user_id;
