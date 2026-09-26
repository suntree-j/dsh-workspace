-- ============================================================
-- Sprint 3 — 离线（批处理）指标表在 Doris 侧的落地
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_3.md 第 3.3 节
--
-- 数据来源：湖仓 lakehouse.ads_* 的 Parquet（MinIO）
--   → Doris S3() TVF 直读装载（见 scripts/load-batch-to-doris.sh）
--
-- !! 为什么单独建一个库 lakehouse_ads，而不是塞进 ecommerce !!
--   ecommerce 库里放的是**实时链路**的表（Flink → Kafka → Routine Load）。
--   两套链路写同一个库、表名还高度相似，会让"这个数到底是实时的还是离线的"
--   变成需要靠记忆判断的事情 —— 这正是数据口径事故的温床。
--   分库之后：库名即链路来源，看板与接口都能一眼区分。
--
-- 字段与实时表的对应关系（对账的前提，二者必须逐字段同形）：
--   lakehouse_ads.ads_batch_trade_1m  ↔  ecommerce.ads_realtime_trade_1m
--   lakehouse_ads.ads_batch_category_1m ↔ ecommerce.ads_realtime_category_1m
--
-- 模型选择：
--   UNIQUE KEY + merge-on-write —— 与实时表一致。
--   批处理重跑时同窗口会被覆盖（幂等）；即使装载脚本漏了 TRUNCATE，
--   重复装载也不会产生重复行。
--   单 BE 必须显式 replication_num = 1（Doris 4.1.4 无 default_replication_num）。
-- ============================================================

-- 说明：Doris 的 CREATE DATABASE **不支持** MySQL 那样的
--   `CREATE DATABASE x COMMENT '...'` 写法（实测报
--    mismatched input 'COMMENT' expecting {<EOF>, ';'}），
--   所以库级说明只能写成 SQL 注释（就是下面这段）。
CREATE DATABASE IF NOT EXISTS lakehouse_ads;

-- ------------------------------------------------------------
-- 离线交易总览（1 分钟，与实时表同形）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_batch_trade_1m (
    `window_start`        DATETIME      NOT NULL COMMENT '窗口开始（事件时间）',
    `window_end`          DATETIME      NOT NULL COMMENT '窗口结束（事件时间）',
    `gmv`                 DECIMAL(18,2)          COMMENT 'GMV：窗口内下单金额合计',
    `order_cnt`           BIGINT                 COMMENT '订单量',
    `order_user_cnt`      BIGINT                 COMMENT '下单用户数（去重）',
    `avg_order_amount`    DECIMAL(18,2)          COMMENT '客单价 = gmv / order_cnt，分母为 0 时 NULL',
    `payment_cnt`         BIGINT                 COMMENT '支付成功笔数',
    `payment_amount`      DECIMAL(18,2)          COMMENT '支付成功金额',
    `payment_fail_cnt`    BIGINT                 COMMENT '支付失败笔数',
    `payment_success_rate` DECIMAL(10,4)         COMMENT '支付成功率，分母为 0 时 NULL',
    `refund_cnt`          BIGINT                 COMMENT '退款笔数',
    `refund_amount`       DECIMAL(18,2)          COMMENT '退款金额',
    `refund_rate`         DECIMAL(10,4)          COMMENT '退款率 = 退款金额/支付金额，分母为 0 时 NULL'
)
UNIQUE KEY(`window_start`)
DISTRIBUTED BY HASH(`window_start`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 离线交易总览（1 天）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_batch_trade_1d (
    `dt`                  DATE          NOT NULL COMMENT '统计日期',
    `gmv`                 DECIMAL(18,2)          COMMENT '当日 GMV',
    `order_cnt`           BIGINT                 COMMENT '当日订单量',
    `order_user_cnt`      BIGINT                 COMMENT '当日下单用户数（去重）',
    `avg_order_amount`    DECIMAL(18,2)          COMMENT '当日客单价',
    `payment_cnt`         BIGINT                 COMMENT '当日支付成功笔数',
    `payment_amount`      DECIMAL(18,2)          COMMENT '当日支付成功金额',
    `payment_fail_cnt`    BIGINT                 COMMENT '当日支付失败笔数',
    `payment_success_rate` DECIMAL(10,4)         COMMENT '当日支付成功率',
    `refund_cnt`          BIGINT                 COMMENT '当日退款笔数',
    `refund_amount`       DECIMAL(18,2)          COMMENT '当日退款金额',
    `refund_rate`         DECIMAL(10,4)          COMMENT '当日退款率'
)
UNIQUE KEY(`dt`)
DISTRIBUTED BY HASH(`dt`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 离线类目销售（1 分钟，与实时类目表同形）
-- 注意列顺序：UNIQUE KEY 必须是有序前缀
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_batch_category_1m (
    `window_start`    DATETIME      NOT NULL COMMENT '窗口开始（事件时间）',
    `category_name`   VARCHAR(100)  NOT NULL COMMENT '商品类目',
    `window_end`      DATETIME               COMMENT '窗口结束（事件时间）',
    `order_cnt`       BIGINT                 COMMENT '类目订单数',
    `gmv`             DECIMAL(18,2)          COMMENT '类目下单金额合计',
    `total_quantity`  BIGINT                 COMMENT '类目商品件数',
    `avg_order_amount` DECIMAL(18,2)         COMMENT '类目客单价'
)
UNIQUE KEY(`window_start`, `category_name`)
DISTRIBUTED BY HASH(`category_name`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 离线类目销售（1 天）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_batch_category_1d (
    `dt`              DATE          NOT NULL COMMENT '统计日期',
    `category_name`   VARCHAR(100)  NOT NULL COMMENT '商品类目',
    `order_cnt`       BIGINT                 COMMENT '类目订单数',
    `order_user_cnt`  BIGINT                 COMMENT '类目下单用户数（去重）',
    `gmv`             DECIMAL(18,2)          COMMENT '类目下单金额合计',
    `total_quantity`  BIGINT                 COMMENT '类目商品件数',
    `avg_order_amount` DECIMAL(18,2)         COMMENT '类目客单价'
)
UNIQUE KEY(`dt`, `category_name`)
DISTRIBUTED BY HASH(`category_name`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 批流对账结果（1 分钟，逐窗口差异留痕）
--
-- 这张表是"数据可信"的直接证据：看板可以把它做成一个
-- "实时 vs 离线一致性"面板，差异不为 0 时能被立刻看到。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_reconcile_trade_1m (
    `window_start`              DATETIME      NOT NULL COMMENT '窗口开始（事件时间）',
    `realtime_gmv`              DECIMAL(18,2)          COMMENT '实时链路 GMV',
    `batch_gmv`                 DECIMAL(18,2)          COMMENT '离线链路 GMV',
    `diff_gmv`                  DECIMAL(18,2)          COMMENT 'GMV 差异（离线 - 实时）',
    `realtime_order_cnt`        BIGINT                 COMMENT '实时订单量',
    `batch_order_cnt`           BIGINT                 COMMENT '离线订单量',
    `diff_order_cnt`            BIGINT                 COMMENT '订单量差异',
    `realtime_order_user_cnt`   BIGINT                 COMMENT '实时下单用户数',
    `batch_order_user_cnt`      BIGINT                 COMMENT '离线下单用户数',
    `diff_order_user_cnt`       BIGINT                 COMMENT '下单用户数差异',
    `realtime_payment_cnt`      BIGINT                 COMMENT '实时支付成功笔数',
    `batch_payment_cnt`         BIGINT                 COMMENT '离线支付成功笔数',
    `diff_payment_cnt`          BIGINT                 COMMENT '支付笔数差异',
    `realtime_payment_amount`   DECIMAL(18,2)          COMMENT '实时支付金额',
    `batch_payment_amount`      DECIMAL(18,2)          COMMENT '离线支付金额',
    `diff_payment_amount`       DECIMAL(18,2)          COMMENT '支付金额差异',
    `realtime_payment_fail_cnt` BIGINT                 COMMENT '实时支付失败笔数',
    `batch_payment_fail_cnt`    BIGINT                 COMMENT '离线支付失败笔数',
    `diff_payment_fail_cnt`     BIGINT                 COMMENT '支付失败笔数差异',
    `realtime_refund_cnt`       BIGINT                 COMMENT '实时退款笔数',
    `batch_refund_cnt`          BIGINT                 COMMENT '离线退款笔数',
    `diff_refund_cnt`           BIGINT                 COMMENT '退款笔数差异',
    `realtime_refund_amount`    DECIMAL(18,2)          COMMENT '实时退款金额',
    `batch_refund_amount`       DECIMAL(18,2)          COMMENT '离线退款金额',
    `diff_refund_amount`        DECIMAL(18,2)          COMMENT '退款金额差异',
    `is_match`                  BOOLEAN                COMMENT '本窗口是否完全一致',
    `compared_at`               DATETIME               COMMENT '对账执行时间'
)
UNIQUE KEY(`window_start`)
DISTRIBUTED BY HASH(`window_start`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 批流对账汇总（追加式：保留历史批次）
--
-- !! 这张表**不做 TRUNCATE** !!
--   负载脚本只做 INSERT。每次对账产生一行，
--   历时记录本身就是"数据可信度"的证据链（什么时候对过账、结论如何）。
--   因此 key 用 batch_id，而不是日期——同一天可能对账多次。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_reconcile_summary (
    `batch_id`            VARCHAR(64)   NOT NULL COMMENT '对账批次标识',
    `compared_at`         DATETIME               COMMENT '对账执行时间',
    `scope_start`         DATETIME               COMMENT '对账区间起点',
    `scope_end`           DATETIME               COMMENT '对账区间终点',
    `realtime_windows`    BIGINT                 COMMENT '实时侧窗口数',
    `batch_windows`       BIGINT                 COMMENT '离线侧窗口数',
    `matched_windows`     BIGINT                 COMMENT '一致窗口数',
    `mismatched_windows`  BIGINT                 COMMENT '不一致窗口数',
    `first_mismatch_at`   DATETIME               COMMENT '首个不一致窗口',
    `realtime_total_gmv`  DECIMAL(18,2)          COMMENT '实时侧 GMV 合计',
    `batch_total_gmv`     DECIMAL(18,2)          COMMENT '离线侧 GMV 合计',
    `is_pass`             BOOLEAN                COMMENT '总体是否通过'
)
UNIQUE KEY(`batch_id`)
DISTRIBUTED BY HASH(`batch_id`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);
