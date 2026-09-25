-- ============================================================
-- 电商业务库 schema
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- Sprint：0
-- 依据：docs/sprint/SPRINT_0.md 第 7 节
--
-- 说明：
--   本文件由 MySQL 官方镜像的 /docker-entrypoint-initdb.d 机制
--   在数据目录为空时自动执行一次。
--   文件已通过 docker-compose.yml 挂载进容器，禁止手工修改
--   容器内数据库结构 —— 所有 DDL 必须进入 Git。
--
-- 注意：MYSQL_DATABASE=ecommerce 已由镜像根据环境变量创建，
--       此处显式声明以保证脚本可独立执行。
-- ============================================================

CREATE DATABASE IF NOT EXISTS `ecommerce`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `ecommerce`;

-- ============================================================
-- user — 用户主数据
-- ============================================================
CREATE TABLE IF NOT EXISTS `user` (
    `user_id`       BIGINT       NOT NULL COMMENT '用户ID',
    `username`      VARCHAR(100) NOT NULL COMMENT '用户名',
    `gender`        TINYINT      DEFAULT NULL COMMENT '性别 0-未知 1-男 2-女',
    `age`           INT          DEFAULT NULL COMMENT '年龄',
    `province`      VARCHAR(50)  DEFAULT NULL COMMENT '省份',
    `city`          VARCHAR(50)  DEFAULT NULL COMMENT '城市',
    `user_level`    VARCHAR(20)  DEFAULT NULL COMMENT '会员等级',
    `register_time` DATETIME     DEFAULT NULL COMMENT '注册时间',
    `update_time`   DATETIME     DEFAULT NULL COMMENT '更新时间',
    PRIMARY KEY (`user_id`),
    KEY `idx_user_register_time` (`register_time`),
    KEY `idx_user_province` (`province`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '用户主数据';

-- ============================================================
-- product — 商品主数据
-- ============================================================
CREATE TABLE IF NOT EXISTS `product` (
    `product_id`    BIGINT       NOT NULL COMMENT '商品ID',
    `product_name`  VARCHAR(200) NOT NULL COMMENT '商品名称',
    `category_id`   BIGINT       DEFAULT NULL COMMENT '类目ID',
    `category_name` VARCHAR(100) DEFAULT NULL COMMENT '类目名称',
    `brand`         VARCHAR(100) DEFAULT NULL COMMENT '品牌',
    `price`         DECIMAL(18,2) DEFAULT NULL COMMENT '售价',
    `cost`          DECIMAL(18,2) DEFAULT NULL COMMENT '成本价',
    `status`        TINYINT      DEFAULT 1 COMMENT '状态 0-下架 1-上架',
    `create_time`   DATETIME     DEFAULT NULL COMMENT '创建时间',
    `update_time`   DATETIME     DEFAULT NULL COMMENT '更新时间',
    PRIMARY KEY (`product_id`),
    KEY `idx_product_category_id` (`category_id`),
    KEY `idx_product_status` (`status`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '商品主数据';

-- ============================================================
-- orders — 订单事实表
--   user_id    -> user.user_id
--   product_id -> product.product_id
-- ============================================================
CREATE TABLE IF NOT EXISTS `orders` (
    `order_id`    BIGINT        NOT NULL COMMENT '订单ID',
    `user_id`     BIGINT        NOT NULL COMMENT '用户ID',
    `product_id`  BIGINT        NOT NULL COMMENT '商品ID',
    `quantity`    INT           NOT NULL COMMENT '购买数量',
    `amount`      DECIMAL(18,2) NOT NULL COMMENT '订单金额',
    `status`      VARCHAR(30)   DEFAULT NULL COMMENT '订单状态',
    `create_time` DATETIME      DEFAULT NULL COMMENT '下单时间',
    `pay_time`    DATETIME      DEFAULT NULL COMMENT '支付时间',
    `update_time` DATETIME      DEFAULT NULL COMMENT '更新时间',
    PRIMARY KEY (`order_id`),
    KEY `idx_orders_user_id` (`user_id`),
    KEY `idx_orders_product_id` (`product_id`),
    KEY `idx_orders_create_time` (`create_time`),
    KEY `idx_orders_status` (`status`),
    CONSTRAINT `fk_orders_user`    FOREIGN KEY (`user_id`)    REFERENCES `user` (`user_id`),
    CONSTRAINT `fk_orders_product` FOREIGN KEY (`product_id`) REFERENCES `product` (`product_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '订单事实表';

-- ============================================================
-- payment — 支付事实表
--   order_id -> orders.order_id
-- ============================================================
CREATE TABLE IF NOT EXISTS `payment` (
    `payment_id`     BIGINT        NOT NULL COMMENT '支付ID',
    `order_id`       BIGINT        NOT NULL COMMENT '订单ID',
    `user_id`        BIGINT        NOT NULL COMMENT '用户ID（冗余，便于流式计算免 join）',
    `amount`         DECIMAL(18,2) NOT NULL COMMENT '支付金额',
    `payment_method` VARCHAR(30)   DEFAULT NULL COMMENT '支付方式',
    `payment_status` VARCHAR(30)   DEFAULT NULL COMMENT '支付状态',
    `payment_time`   DATETIME      DEFAULT NULL COMMENT '支付时间',
    PRIMARY KEY (`payment_id`),
    KEY `idx_payment_order_id` (`order_id`),
    KEY `idx_payment_user_id` (`user_id`),
    KEY `idx_payment_time` (`payment_time`),
    CONSTRAINT `fk_payment_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`order_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '支付事实表';

-- ============================================================
-- refund — 退款事实表
--   order_id -> orders.order_id
-- ============================================================
CREATE TABLE IF NOT EXISTS `refund` (
    `refund_id`     BIGINT        NOT NULL COMMENT '退款ID',
    `order_id`      BIGINT        NOT NULL COMMENT '订单ID',
    `user_id`       BIGINT        NOT NULL COMMENT '用户ID（冗余，便于流式计算免 join）',
    `refund_amount` DECIMAL(18,2) DEFAULT NULL COMMENT '退款金额',
    `refund_reason` VARCHAR(200)  DEFAULT NULL COMMENT '退款原因',
    `refund_status` VARCHAR(30)   DEFAULT NULL COMMENT '退款状态',
    `refund_time`   DATETIME      DEFAULT NULL COMMENT '退款时间',
    PRIMARY KEY (`refund_id`),
    KEY `idx_refund_order_id` (`order_id`),
    KEY `idx_refund_user_id` (`user_id`),
    KEY `idx_refund_time` (`refund_time`),
    CONSTRAINT `fk_refund_order` FOREIGN KEY (`order_id`) REFERENCES `orders` (`order_id`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '退款事实表';
