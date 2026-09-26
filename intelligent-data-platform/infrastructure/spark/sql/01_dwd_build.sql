-- ============================================================
-- Sprint 3 — ODS → DWD 清洗作业（Spark SQL）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_3.md 第 3.1 / 3.2 节
-- 目标表定义：sql/hive/02_dwd_tables.sql
--
-- 本文件只做 DWD 层该做的四件事：去重、清洗、维度补全、统一命名。
-- **不做聚合**（聚合属于 DWS）。
--
-- 幂等：
--   事实表按 dt 分区覆盖（动态分区），维表用固定哨兵分区重新写入。
--   spark.sql.sources.partitionOverwriteMode=dynamic 由作业显式设置，
--   否则 INSERT OVERWRITE 会把整表清空。
-- ============================================================

-- ------------------------------------------------------------
-- 1. 用户主数据：按 user_id 去重（保留 update_time 最新一行）
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_user_dedup;
CREATE OR REPLACE TEMPORARY VIEW v_user_dedup AS
SELECT user_id, username, gender, age, province, city, user_level,
       register_time, update_time
FROM (
    SELECT u.*,
           ROW_NUMBER() OVER (
               PARTITION BY user_id
               ORDER BY COALESCE(update_time, register_time, TIMESTAMP'1970-01-01 00:00:00') DESC,
                        register_time DESC
           ) AS rn
    FROM lakehouse.ods_user u
) t
WHERE rn = 1;

-- 维表用一个固定的哨兵分区：全量快照，每次重建。
-- 为什么不用真实日期分区：维表要参与**全量**join，
-- 若按 register_time 分区，join 时必须扫描全部历史分区，
-- 分区反而失去意义；用哨兵分区可让"重建整张维表"语义明确。
INSERT OVERWRITE TABLE lakehouse.dwd_user_detail PARTITION (dt = '9999-12-31')
SELECT user_id,
       username,
       gender,
       age,
       province,
       city,
       user_level,
       register_time,
       update_time
FROM v_user_dedup
WHERE user_id IS NOT NULL
  AND username IS NOT NULL;

-- ------------------------------------------------------------
-- 2. 商品主数据：按 product_id 去重
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_product_dedup;
CREATE OR REPLACE TEMPORARY VIEW v_product_dedup AS
SELECT product_id, product_name, category_id, category_name, brand,
       price, cost, status, create_time, update_time
FROM (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY product_id
               ORDER BY COALESCE(update_time, create_time, TIMESTAMP'1970-01-01 00:00:00') DESC,
                        create_time DESC
           ) AS rn
    FROM lakehouse.ods_product p
) t
WHERE rn = 1;

INSERT OVERWRITE TABLE lakehouse.dwd_product_detail PARTITION (dt = '9999-12-31')
SELECT product_id,
       product_name,
       category_id,
       category_name,
       brand,
       price,
       cost,
       status,
       create_time,
       update_time
FROM v_product_dedup
WHERE product_id IS NOT NULL
  AND product_name IS NOT NULL;

-- ------------------------------------------------------------
-- 3. 订单明细
--
-- 事件时间 = create_time（下单即计入 GMV，见 sql/metadata/metrics.md 第 2.1 节）
-- 清洗规则：主键/外键非空、金额非负、下单时间非空
-- 维度补全：category_name 来自商品维表，user_level / province 来自用户维表
--
-- 为什么维度取 DWD 维表而不是 ODS：
--   DWD 维表已经去重过。若直接 join ODS，一个 user_id 有多行时会
--   把订单**放大**成多行（行数对账立刻失败）。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_order_clean;
CREATE OR REPLACE TEMPORARY VIEW v_order_clean AS
SELECT o.order_id,
       o.user_id,
       o.product_id,
       o.quantity,
       o.amount,
       o.status AS order_status,
       o.create_time AS event_time,
       TO_DATE(o.create_time) AS dt
FROM (
    SELECT od.*,
           ROW_NUMBER() OVER (
               PARTITION BY od.order_id
               ORDER BY COALESCE(od.update_time, od.create_time,
                                 TIMESTAMP'1970-01-01 00:00:00') DESC
           ) AS rn
    FROM lakehouse.ods_orders od
) o
WHERE o.rn = 1
  AND o.order_id IS NOT NULL
  AND o.user_id IS NOT NULL
  AND o.product_id IS NOT NULL
  AND o.create_time IS NOT NULL
  AND o.amount IS NOT NULL
  -- 负金额是脏数据；0 元订单在业务上可能（赠品），保留
  AND o.amount >= 0;

INSERT OVERWRITE TABLE lakehouse.dwd_trade_order_detail PARTITION (dt)
SELECT o.order_id,
       o.user_id,
       o.product_id,
       p.category_name,
       u.user_level,
       u.province,
       o.quantity,
       o.amount,
       o.order_status,
       o.event_time,
       CAST(o.dt AS STRING) AS dt
FROM v_order_clean o
LEFT JOIN v_user_dedup u ON o.user_id = u.user_id
LEFT JOIN v_product_dedup p ON o.product_id = p.product_id;

-- ------------------------------------------------------------
-- 4. 支付明细
--
-- !! 事件时间映射是批流对账能否成立的关键 !!
--   源表 payment 只有 payment_time 一个时间字段，支付失败的行它为 NULL。
--   Kafka 事件里 event_time = payment_time（见 data-generator/src/kafka_events.py
--   第 133 行），失败行沿用订单的 pay_time（见 dataset.py 第 362-374 行）。
--   因此这里用三层兜底，与事件侧保持同源：
--       1) payment.payment_time          （成功支付）
--       2) 所属 orders.pay_time          （失败支付）
--       3) 所属 orders.create_time       （极端兜底）
--   如果这里对不上，对账会把"时间映射错"误报成"指标算错"。
--
-- 支付状态：MySQL 用 SUCCESS / FAILED，与 Kafka 事件类型
--   PAYMENT_SUCCESS / PAYMENT_FAILED 一一对应。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_payment_clean;
CREATE OR REPLACE TEMPORARY VIEW v_payment_clean AS
SELECT pay.payment_id,
       pay.order_id,
       pay.user_id,
       pay.amount,
       pay.payment_method,
       pay.payment_status,
       COALESCE(pay.payment_time, o.pay_time, o.create_time) AS event_time
FROM (
    SELECT pd.*,
           ROW_NUMBER() OVER (
               PARTITION BY pd.payment_id
               ORDER BY COALESCE(pd.payment_time, TIMESTAMP'1970-01-01 00:00:00') DESC
           ) AS rn
    FROM lakehouse.ods_payment pd
) pay
LEFT JOIN (
    SELECT order_id, pay_time, create_time
    FROM lakehouse.ods_orders
) o ON pay.order_id = o.order_id
WHERE pay.rn = 1
  AND pay.payment_id IS NOT NULL
  AND pay.order_id IS NOT NULL
  AND pay.user_id IS NOT NULL
  AND pay.amount IS NOT NULL
  AND COALESCE(pay.payment_time, o.pay_time, o.create_time) IS NOT NULL;

INSERT OVERWRITE TABLE lakehouse.dwd_trade_payment_detail PARTITION (dt)
SELECT pay.payment_id,
       pay.order_id,
       pay.user_id,
       u.user_level,
       pay.amount,
       pay.payment_method,
       pay.payment_status,
       pay.event_time,
       CAST(TO_DATE(pay.event_time) AS STRING) AS dt
FROM v_payment_clean pay
LEFT JOIN v_user_dedup u ON pay.user_id = u.user_id;

-- ------------------------------------------------------------
-- 5. 退款明细
-- 事件时间 = refund_time
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_refund_clean;
CREATE OR REPLACE TEMPORARY VIEW v_refund_clean AS
SELECT r.refund_id,
       r.order_id,
       r.user_id,
       r.refund_amount,
       r.refund_reason,
       r.refund_status,
       r.refund_time AS event_time
FROM (
    SELECT rf.*,
           ROW_NUMBER() OVER (
               PARTITION BY rf.refund_id
               ORDER BY COALESCE(rf.refund_time, TIMESTAMP'1970-01-01 00:00:00') DESC
           ) AS rn
    FROM lakehouse.ods_refund rf
) r
WHERE r.rn = 1
  AND r.refund_id IS NOT NULL
  AND r.order_id IS NOT NULL
  AND r.user_id IS NOT NULL
  AND r.refund_amount IS NOT NULL
  AND r.refund_time IS NOT NULL;

INSERT OVERWRITE TABLE lakehouse.dwd_trade_refund_detail PARTITION (dt)
SELECT r.refund_id,
       r.order_id,
       r.user_id,
       u.user_level,
       r.refund_amount,
       r.refund_reason,
       r.refund_status,
       r.event_time,
       CAST(TO_DATE(r.event_time) AS STRING) AS dt
FROM v_refund_clean r
LEFT JOIN v_user_dedup u ON r.user_id = u.user_id;
