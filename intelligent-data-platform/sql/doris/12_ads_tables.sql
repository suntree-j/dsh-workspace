-- ============================================================
-- Sprint 1 — ADS 应用层建表（实时指标）
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_1.md 第 3.4 节与第 4 节（指标口径）
--
-- 数据来源：Flink 1 分钟滚动窗口聚合结果 → Kafka → Routine Load
--
-- 口径唯一性：
--   本文件中的字段语义以 sql/metadata/metrics.md 为唯一权威定义，
--   离线链路（Sprint 3 起）必须产生同名同口径指标。
--
-- 空值约定：
--   分母为 0 的比率类指标（支付成功率、退款率）置 NULL，不置 0，
--   避免下游把"无数据"误读为"比率是 0"。
-- ============================================================

CREATE DATABASE IF NOT EXISTS ecommerce;
USE ecommerce;

-- ------------------------------------------------------------
-- ADS：实时交易总览（1 分钟窗口）
-- 对应指标：GMV / 订单量 / 客单价 / 支付笔数 / 支付金额 /
--           退款笔数 / 退款金额 / 支付成功率 / 退款率
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ads_realtime_trade_1m (
    `window_start`      DATETIME      NOT NULL COMMENT '窗口开始（事件时间）',
    `window_end`        DATETIME      NOT NULL COMMENT '窗口结束（事件时间）',
    `gmv`               DECIMAL(18,2)          COMMENT 'GMV：窗口内下单金额合计',
    `order_cnt`         BIGINT                 COMMENT '订单量：窗口内订单创建事件数',
    `order_user_cnt`    BIGINT                 COMMENT '下单用户数（去重）',
    `avg_order_amount`  DECIMAL(18,2)          COMMENT '客单价 = gmv / order_cnt',
    `payment_cnt`       BIGINT                 COMMENT '支付成功笔数',
    `payment_amount`    DECIMAL(18,2)          COMMENT '支付成功金额',
    `payment_fail_cnt`  BIGINT                 COMMENT '支付失败笔数',
    `payment_success_rate` DECIMAL(10,4)       COMMENT '支付成功率 = 成功/(成功+失败)，分母为0时 NULL',
    `refund_cnt`        BIGINT                 COMMENT '退款笔数',
    `refund_amount`     DECIMAL(18,2)          COMMENT '退款金额',
    `refund_rate`       DECIMAL(10,4)          COMMENT '退款率 = 退款金额/支付金额，分母为0时 NULL'
)
UNIQUE KEY(`window_start`)
DISTRIBUTED BY HASH(`window_start`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- ADS：实时流量
-- 对应指标：UV / PV / 各行为计数 / 转化率
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ads_realtime_traffic_1m (
    `window_start`   DATETIME     NOT NULL COMMENT '窗口开始（事件时间）',
    `window_end`     DATETIME     NOT NULL COMMENT '窗口结束（事件时间）',
    `uv`             BIGINT                COMMENT '去重用户数',
    `pv`             BIGINT                COMMENT '行为事件总数',
    `view_cnt`       BIGINT                COMMENT 'VIEW 次数',
    `click_cnt`      BIGINT                COMMENT 'CLICK 次数',
    `cart_cnt`       BIGINT                COMMENT 'CART 次数',
    `favorite_cnt`   BIGINT                COMMENT 'FAVORITE 次数',
    `buy_cnt`        BIGINT                COMMENT 'BUY 次数',
    `click_rate`     DECIMAL(10,4)         COMMENT '点击率 = click/view，分母为0时 NULL',
    `cart_rate`      DECIMAL(10,4)         COMMENT '加购率 = cart/click，分母为0时 NULL',
    `buy_rate`       DECIMAL(10,4)         COMMENT '购买转化率 = buy/cart，分母为0时 NULL'
)
UNIQUE KEY(`window_start`)
DISTRIBUTED BY HASH(`window_start`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- ADS：实时类目销售（1 分钟窗口 × 类目）
--
-- !! 注意列顺序 !!
--   Doris 要求 UNIQUE KEY 的列必须是 schema 的**有序前缀**。
--   本表 key 为 (window_start, category_name)，因此这两列必须
--   声明在最前面；否则报：
--     Key columns should be a ordered prefix of the schema.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ads_realtime_category_1m (
    `window_start`   DATETIME      NOT NULL COMMENT '窗口开始（事件时间）',
    `category_name`  VARCHAR(100)  NOT NULL COMMENT '商品类目',
    `window_end`     DATETIME               COMMENT '窗口结束（事件时间）',
    `order_cnt`      BIGINT                 COMMENT '订单数',
    `gmv`            DECIMAL(18,2)          COMMENT '下单金额合计',
    `total_quantity` BIGINT                 COMMENT '商品件数',
    `avg_order_amount` DECIMAL(18,2)        COMMENT '类目客单价'
)
UNIQUE KEY(`window_start`, `category_name`)
DISTRIBUTED BY HASH(`category_name`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);
