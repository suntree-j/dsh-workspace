-- ============================================================
-- Sprint 1 — DWD 明细层建表
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_1.md 第 3.2 节
--
-- 数据来源：Kafka metrics topic → Doris Routine Load
--   dwd_trade_order_detail     ← metrics_dwd_trade_order_detail
--   dwd_trade_payment_detail   ← metrics_dwd_trade_payment_detail
--   dwd_trade_refund_detail    ← metrics_dwd_trade_refund_detail
--   dwd_traffic_behavior_detail← metrics_dwd_traffic_behavior_detail
--
-- 模型选择说明：
--   统一使用 UNIQUE KEY + merge-on-write：
--     - 以业务主键（订单号/支付号/退款号/事件号）为 key，
--       事件重放或重复投递时自动覆盖，实现幂等；
--     - merge-on-write 保证读时已是合并后的最新值。
--
-- 单 BE 环境必须显式声明 replication_num = 1
-- （Doris 4.1.4 不存在 default_replication_num 系统变量，
--   不加 PROPERTIES 会因默认 3 副本而建表失败）。
-- ============================================================

CREATE DATABASE IF NOT EXISTS ecommerce;
USE ecommerce;

-- ------------------------------------------------------------
-- 订单明细
-- 粒度：一行一个订单（ORDER_CREATED 事件）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dwd_trade_order_detail (
    `order_id`     BIGINT        NOT NULL COMMENT '订单ID',
    `user_id`      BIGINT        NOT NULL COMMENT '用户ID',
    `product_id`   BIGINT        NOT NULL COMMENT '商品ID',
    `category_name` VARCHAR(100)           COMMENT '商品类目（维表补全）',
    `user_level`   VARCHAR(20)             COMMENT '会员等级（维表补全）',
    `province`     VARCHAR(50)             COMMENT '用户省份（维表补全）',
    `quantity`     INT                     COMMENT '购买数量',
    `amount`       DECIMAL(18,2)           COMMENT '订单金额',
    `event_id`     VARCHAR(64)             COMMENT '事件ID',
    `event_time`   DATETIME                COMMENT '事件时间（业务时间）',
    `dt`           DATE                    COMMENT '分区日期'
)
UNIQUE KEY(`order_id`)
DISTRIBUTED BY HASH(`order_id`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 支付明细
-- 粒度：一行一笔支付
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dwd_trade_payment_detail (
    `payment_id`     BIGINT        NOT NULL COMMENT '支付ID',
    `order_id`       BIGINT        NOT NULL COMMENT '订单ID',
    `user_id`        BIGINT        NOT NULL COMMENT '用户ID',
    `user_level`     VARCHAR(20)            COMMENT '会员等级（维表补全）',
    `amount`         DECIMAL(18,2)          COMMENT '支付金额',
    `payment_method` VARCHAR(30)            COMMENT '支付方式',
    `payment_status` VARCHAR(30)            COMMENT '支付状态 SUCCESS/FAILED',
    `event_id`       VARCHAR(64)            COMMENT '事件ID',
    `event_time`     DATETIME               COMMENT '事件时间',
    `dt`             DATE                   COMMENT '分区日期'
)
UNIQUE KEY(`payment_id`)
DISTRIBUTED BY HASH(`payment_id`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 退款明细
-- 粒度：一行一笔退款
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dwd_trade_refund_detail (
    `refund_id`     BIGINT        NOT NULL COMMENT '退款ID',
    `order_id`      BIGINT        NOT NULL COMMENT '订单ID',
    `user_id`       BIGINT        NOT NULL COMMENT '用户ID',
    `user_level`    VARCHAR(20)            COMMENT '会员等级（维表补全）',
    `refund_amount` DECIMAL(18,2)          COMMENT '退款金额',
    `event_id`      VARCHAR(64)            COMMENT '事件ID',
    `event_time`    DATETIME               COMMENT '事件时间',
    `dt`            DATE                   COMMENT '分区日期'
)
UNIQUE KEY(`refund_id`)
DISTRIBUTED BY HASH(`refund_id`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 行为明细
-- 粒度：一行一个行为事件
-- 说明：行为事件量大（默认 20000 条/批），是 DWD 中数据量最大的表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dwd_traffic_behavior_detail (
    `event_id`     VARCHAR(64)   NOT NULL COMMENT '事件ID',
    `event_type`   VARCHAR(20)            COMMENT '行为类型 VIEW/CLICK/CART/FAVORITE/BUY',
    `user_id`      BIGINT                 COMMENT '用户ID',
    `product_id`   BIGINT                 COMMENT '商品ID',
    `category_name` VARCHAR(100)          COMMENT '商品类目（维表补全）',
    `device`       VARCHAR(20)            COMMENT '设备类型',
    `province`     VARCHAR(50)            COMMENT '省份',
    `event_time`   DATETIME               COMMENT '事件时间',
    `dt`           DATE                   COMMENT '分区日期'
)
UNIQUE KEY(`event_id`)
DISTRIBUTED BY HASH(`event_id`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 维表：商品
-- 来源：MySQL ecommerce.product 一次性导入
-- 模型用 UNIQUE KEY 便于后续 upsert 更新
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_product (
    `product_id`    BIGINT       NOT NULL COMMENT '商品ID',
    `product_name`  VARCHAR(200)          COMMENT '商品名称',
    `category_id`   BIGINT                COMMENT '类目ID',
    `category_name` VARCHAR(100)          COMMENT '类目名称',
    `brand`         VARCHAR(100)          COMMENT '品牌',
    `price`         DECIMAL(18,2)         COMMENT '售价',
    `cost`          DECIMAL(18,2)         COMMENT '成本价',
    `status`        TINYINT               COMMENT '状态'
)
UNIQUE KEY(`product_id`)
DISTRIBUTED BY HASH(`product_id`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 维表：用户
-- 来源：MySQL ecommerce.user 一次性导入
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_user (
    `user_id`     BIGINT       NOT NULL COMMENT '用户ID',
    `username`    VARCHAR(100)          COMMENT '用户名',
    `gender`      TINYINT               COMMENT '性别',
    `age`         INT                   COMMENT '年龄',
    `province`    VARCHAR(50)           COMMENT '省份',
    `city`        VARCHAR(50)           COMMENT '城市',
    `user_level`  VARCHAR(20)           COMMENT '会员等级'
)
UNIQUE KEY(`user_id`)
DISTRIBUTED BY HASH(`user_id`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);
