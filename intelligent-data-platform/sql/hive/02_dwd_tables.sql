-- ============================================================
-- Sprint 3 — 湖仓 DWD 层（明细层）建表
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_3.md 第 3 节
--
-- 职责（只做这四件事，不做聚合）：
--   1. 去重：以业务主键保留最新一行（ODS 是全量快照，同键可能多行）
--   2. 清洗：主键/外键/金额非空校验，非法行不进 DWD
--   3. 维度补全：从 dim 维表补 user_level / province / category_name
--   4. 统一命名：业务时间统一叫 event_time，派生分区列 dt
--
-- 存储：s3a://lakehouse/warehouse/dwd/<表名>/dt=<YYYY-MM-DD>/  （Parquet，按天分区）
--
-- 为什么按 dt 分区：
--   MySQL 的事实时间跨度约 700 天，按天分区后重跑可以只覆盖指定日期，
--   不必整表重写（幂等性靠 INSERT OVERWRITE 分区实现）。
--
-- 为什么用 EXTERNAL TABLE：与 ODS 一致 —— DROP 只删元数据不删数据。
--
-- 与实时链路的字段对应（对账前提）：
--   lakehouse.dwd_trade_order_detail  ↔ ecommerce.dwd_trade_order_detail
--   lakehouse.dwd_trade_payment_detail ↔ ecommerce.dwd_trade_payment_detail
--   lakehouse.dwd_trade_refund_detail  ↔ ecommerce.dwd_trade_refund_detail
--   实时表见 sql/doris/10_dwd_tables.sql，字段名必须保持一致。
--
-- 幂等：全部 IF NOT EXISTS，可重复执行。
-- ============================================================

CREATE DATABASE IF NOT EXISTS lakehouse
COMMENT '湖仓库：ODS（贴源）→ DWD → DWS → ADS'
LOCATION 's3a://lakehouse/warehouse';

-- ------------------------------------------------------------
-- DWD：用户明细（源：ods_user）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dwd_user_detail (
    user_id       BIGINT        COMMENT '用户ID',
    username      STRING        COMMENT '用户名',
    gender        INT           COMMENT '性别 0-未知 1-男 2-女',
    age           INT           COMMENT '年龄',
    province      STRING        COMMENT '省份',
    city          STRING        COMMENT '城市',
    user_level    STRING        COMMENT '会员等级',
    register_time TIMESTAMP     COMMENT '注册时间',
    update_time   TIMESTAMP     COMMENT '更新时间'
)
COMMENT 'DWD-用户明细（去重 + 清洗）'
PARTITIONED BY (dt STRING COMMENT '分区日期（*静态*分区：用户主数据量小且要参与全量 join）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dwd/user_detail';

-- ------------------------------------------------------------
-- DWD：商品明细（源：ods_product）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dwd_product_detail (
    product_id    BIGINT        COMMENT '商品ID',
    product_name  STRING        COMMENT '商品名称',
    category_id   BIGINT        COMMENT '类目ID',
    category_name STRING        COMMENT '类目名称',
    brand         STRING        COMMENT '品牌',
    price         DECIMAL(18,2) COMMENT '售价',
    cost          DECIMAL(18,2) COMMENT '成本价',
    status        INT           COMMENT '状态 0-下架 1-上架',
    create_time   TIMESTAMP     COMMENT '创建时间',
    update_time   TIMESTAMP     COMMENT '更新时间'
)
COMMENT 'DWD-商品明细（去重 + 清洗）'
PARTITIONED BY (dt STRING COMMENT '分区日期（*静态*分区：商品主数据量小且要参与全量 join）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dwd/product_detail';

-- ------------------------------------------------------------
-- DWD：订单明细（源：ods_orders + 维表补全）
--
-- event_time = orders.create_time（下单即计入 GMV，见 metrics.md 第 2.1 节）
-- 粒度：一行一个订单
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dwd_trade_order_detail (
    order_id       BIGINT        COMMENT '订单ID',
    user_id        BIGINT        COMMENT '用户ID',
    product_id     BIGINT        COMMENT '商品ID',
    category_name  STRING        COMMENT '商品类目（维表补全）',
    user_level     STRING        COMMENT '会员等级（维表补全）',
    province       STRING        COMMENT '用户省份（维表补全）',
    quantity       INT           COMMENT '购买数量',
    amount         DECIMAL(18,2) COMMENT '订单金额',
    order_status   STRING        COMMENT '订单当前状态（快照值，非事件语义）',
    event_time     TIMESTAMP     COMMENT '事件时间（= 下单时间）'
)
COMMENT 'DWD-订单明细（GMV / 订单量的事实来源）'
PARTITIONED BY (dt STRING COMMENT '分区日期（由 event_time 派生）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dwd/trade_order_detail';

-- ------------------------------------------------------------
-- DWD：支付明细（源：ods_payment + 维表补全）
--
-- event_time 的取值规则（**对账能否成立的关键**）：
--   源表 payment 只有 payment_time 一个时间字段，
--   支付失败的行 payment_time 为 NULL。
--   数据生成器对失败行沿用了订单的 pay_time
--   （见 data-generator/src/dataset.py 第 362-374 行），
--   因此这里用 COALESCE(pay_time, orders.pay_time, create_time) 兜底，
--   与 Kafka 事件中的 event_time 保持同源。
-- 校验方式：与实时 dwd_trade_payment_detail 的 event_time 分布逐窗口比对。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dwd_trade_payment_detail (
    payment_id     BIGINT        COMMENT '支付ID',
    order_id       BIGINT        COMMENT '订单ID',
    user_id        BIGINT        COMMENT '用户ID',
    user_level     STRING        COMMENT '会员等级（维表补全）',
    amount         DECIMAL(18,2) COMMENT '支付金额',
    payment_method STRING        COMMENT '支付方式',
    payment_status STRING        COMMENT '支付状态 SUCCESS / FAILED',
    event_time     TIMESTAMP     COMMENT '事件时间（成功=payment_time，失败=兜底时间）'
)
COMMENT 'DWD-支付明细（支付笔数 / 金额 / 成功率的事实来源）'
PARTITIONED BY (dt STRING COMMENT '分区日期（由 event_time 派生）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dwd/trade_payment_detail';

-- ------------------------------------------------------------
-- DWD：退款明细（源：ods_refund + 维表补全）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dwd_trade_refund_detail (
    refund_id     BIGINT        COMMENT '退款ID',
    order_id      BIGINT        COMMENT '订单ID',
    user_id       BIGINT        COMMENT '用户ID',
    user_level    STRING        COMMENT '会员等级（维表补全）',
    refund_amount DECIMAL(18,2) COMMENT '退款金额',
    refund_reason STRING        COMMENT '退款原因',
    refund_status STRING        COMMENT '退款状态',
    event_time    TIMESTAMP     COMMENT '事件时间（= 退款时间）'
)
COMMENT 'DWD-退款明细（退款笔数 / 金额 / 退款率的事实来源）'
PARTITIONED BY (dt STRING COMMENT '分区日期（由 event_time 派生）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dwd/trade_refund_detail';
