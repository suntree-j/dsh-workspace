-- ============================================================
-- Sprint 2 — 湖仓 ODS 层（贴源层）建表
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_2.md 第 3 节
--
-- 职责：
--   ODS 层**结构与源系统一致**（不做清洗、不做维度补全），
--   只做两件事：类型归一（MySQL TINYINT → INT 等）+ 落到湖仓存储。
--   清洗与维度补全属于 DWD（Sprint 3）。
--
-- 存储：s3a://lakehouse/warehouse/ods/<表名>/   （Parquet）
--
-- 为什么用 EXTERNAL TABLE：
--   DROP TABLE 只删元数据、不删数据，重跑抽取作业不会被误清空；
--   同时允许后续 Sprint 5 把同一份数据挂成 Iceberg 表。
--
-- 为什么金额用 DECIMAL(18,2)：
--   与 MySQL 源表、Doris 指标表保持同一精度；用 double 会在对数时出现
--   0.01 级别的漂移，直接破坏"实时与离线精确对账"这个目标。
--
-- 幂等：全部 IF NOT EXISTS，可重复执行。
-- ============================================================

CREATE DATABASE IF NOT EXISTS lakehouse
COMMENT '湖仓库：ODS（贴源）→ DWD → DWS → ADS（Sprint 3 起逐步建立）'
LOCATION 's3a://lakehouse/warehouse';

-- ------------------------------------------------------------
-- ODS：用户主数据（源：ecommerce.user）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ods_user (
    user_id       BIGINT       COMMENT '用户ID',
    username      STRING       COMMENT '用户名',
    gender        INT          COMMENT '性别（源为 TINYINT，归一为 INT 以避免跨引擎兼容问题）',
    age           INT          COMMENT '年龄',
    province      STRING       COMMENT '省份',
    city          STRING       COMMENT '城市',
    user_level    STRING       COMMENT '会员等级',
    register_time TIMESTAMP    COMMENT '注册时间',
    update_time   TIMESTAMP    COMMENT '更新时间'
)
COMMENT 'ODS-用户主数据（贴源，未清洗）'
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ods/user';

-- ------------------------------------------------------------
-- ODS：商品主数据（源：ecommerce.product）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ods_product (
    product_id    BIGINT       COMMENT '商品ID',
    product_name  STRING       COMMENT '商品名称',
    category_id   BIGINT       COMMENT '类目ID',
    category_name STRING       COMMENT '类目名称',
    brand         STRING       COMMENT '品牌',
    price         DECIMAL(18,2) COMMENT '售价',
    cost          DECIMAL(18,2) COMMENT '成本价',
    status        INT          COMMENT '状态',
    create_time   TIMESTAMP    COMMENT '创建时间',
    update_time   TIMESTAMP    COMMENT '更新时间'
)
COMMENT 'ODS-商品主数据（贴源，未清洗）'
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ods/product';

-- ------------------------------------------------------------
-- ODS：订单事实（源：ecommerce.orders）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ods_orders (
    order_id     BIGINT        COMMENT '订单ID',
    user_id      BIGINT        COMMENT '用户ID',
    product_id   BIGINT        COMMENT '商品ID',
    quantity     INT           COMMENT '购买数量',
    amount       DECIMAL(18,2) COMMENT '订单金额',
    status       STRING        COMMENT '订单状态',
    create_time  TIMESTAMP     COMMENT '下单时间',
    pay_time     TIMESTAMP     COMMENT '支付时间（可能为空）',
    update_time  TIMESTAMP     COMMENT '更新时间'
)
COMMENT 'ODS-订单事实（贴源，未清洗）'
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ods/orders';

-- ------------------------------------------------------------
-- ODS：支付事实（源：ecommerce.payment）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ods_payment (
    payment_id     BIGINT        COMMENT '支付ID',
    order_id       BIGINT        COMMENT '订单ID',
    user_id        BIGINT        COMMENT '用户ID',
    amount         DECIMAL(18,2) COMMENT '支付金额',
    payment_method STRING        COMMENT '支付方式',
    payment_status STRING        COMMENT '支付状态',
    payment_time   TIMESTAMP     COMMENT '支付时间'
)
COMMENT 'ODS-支付事实（贴源，未清洗）'
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ods/payment';

-- ------------------------------------------------------------
-- ODS：退款事实（源：ecommerce.refund）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ods_refund (
    refund_id     BIGINT        COMMENT '退款ID',
    order_id      BIGINT        COMMENT '订单ID',
    user_id       BIGINT        COMMENT '用户ID',
    refund_amount DECIMAL(18,2) COMMENT '退款金额',
    refund_reason STRING        COMMENT '退款原因',
    refund_status STRING        COMMENT '退款状态',
    refund_time   TIMESTAMP     COMMENT '退款时间'
)
COMMENT 'ODS-退款事实（贴源，未清洗）'
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ods/refund';
