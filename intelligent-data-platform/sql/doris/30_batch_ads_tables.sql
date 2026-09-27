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

-- ============================================================
-- Sprint 5 — 流量域离线指标 + 对账结果在 Doris 侧的落地
-- ============================================================
--
-- 数据来源：湖仓 lakehouse.ads_traffic_1m / ads_traffic_1d /
--           ads_reconcile_traffic_* 的 Parquet（MinIO）
--   → Doris S3() TVF 直读装载（见 scripts/load-batch-to-doris.sh）
--
-- 字段与实时表的对应关系（对账的前提，二者必须逐字段同形）：
--   lakehouse_ads.ads_traffic_1m ↔ ecommerce.ads_realtime_traffic_1m
--   字段名 / 类型 / **顺序**都一致（含 uv/pv 的先后顺序）。
--
-- !! 空值约定（与 metrics.md 第 3 节、实时链路一致）!!
--   可加指标（pv / 各行为计数）无事件时为 0，不是 NULL；
--   比率指标（click_rate / cart_rate / buy_rate）分母为 0 时为 NULL。
--   实时表用的是 DATETIME NOT NULL 的 key + 可空的值列，
--   这里保持一致，否则同一个窗口的 NULL 形态不同会被对账当成差异。
-- ============================================================

-- ------------------------------------------------------------
-- 离线流量（1 分钟，与实时表同形）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_traffic_1m (
    `window_start`  DATETIME      NOT NULL COMMENT '窗口开始（事件时间）',
    `window_end`    DATETIME      NOT NULL COMMENT '窗口结束（事件时间）',
    `uv`            BIGINT                 COMMENT '去重用户数（跨窗口不可加）',
    `pv`            BIGINT                 COMMENT '行为事件总数（可加）',
    `view_cnt`      BIGINT                 COMMENT 'VIEW 次数（可加）',
    `click_cnt`     BIGINT                 COMMENT 'CLICK 次数（可加）',
    `cart_cnt`      BIGINT                 COMMENT 'CART 次数（可加）',
    `favorite_cnt`  BIGINT                 COMMENT 'FAVORITE 次数（可加）',
    `buy_cnt`       BIGINT                 COMMENT 'BUY 次数（可加）',
    `click_rate`    DECIMAL(10,4)          COMMENT '点击率 = click/view，分母为0时 NULL',
    `cart_rate`     DECIMAL(10,4)          COMMENT '加购率 = cart/click，分母为0时 NULL',
    `buy_rate`      DECIMAL(10,4)          COMMENT '购买转化率 = buy/cart，分母为0时 NULL'
)
UNIQUE KEY(`window_start`)
DISTRIBUTED BY HASH(`window_start`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 离线流量（1 天）
--
-- !! uv 是去重指标：本表按天去重，**不可**由 ads_traffic_1m 的 uv 相加得到 !!
--   湖仓侧的 1d 表是从明细精确去重产出的（见 07_traffic_ads.sql）。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_traffic_1d (
    `dt`            DATE          NOT NULL COMMENT '统计日期',
    `uv`            BIGINT                 COMMENT '当日去重用户数（不可加）',
    `pv`            BIGINT                 COMMENT '当日行为事件总数（可加）',
    `view_cnt`      BIGINT                 COMMENT 'VIEW 次数（可加）',
    `click_cnt`     BIGINT                 COMMENT 'CLICK 次数（可加）',
    `cart_cnt`      BIGINT                 COMMENT 'CART 次数（可加）',
    `favorite_cnt`  BIGINT                 COMMENT 'FAVORITE 次数（可加）',
    `buy_cnt`       BIGINT                 COMMENT 'BUY 次数（可加）',
    `click_rate`    DECIMAL(10,4)          COMMENT '点击率',
    `cart_rate`     DECIMAL(10,4)          COMMENT '加购率',
    `buy_rate`      DECIMAL(10,4)          COMMENT '购买转化率'
)
UNIQUE KEY(`dt`)
DISTRIBUTED BY HASH(`dt`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 批流对账结果（流量域，1 分钟，逐窗口差异留痕）
--
-- 与交易域对账表分表存放：交易域那张已验收，加一个域列会把它拉回未验收状态。
-- 见 sql/hive/05_reconcile_tables.sql 的说明。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_reconcile_traffic_1m (
    `window_start`           DATETIME      NOT NULL COMMENT '窗口开始（事件时间）',
    `realtime_uv`            BIGINT                 COMMENT '实时链路 UV',
    `batch_uv`               BIGINT                 COMMENT '离线链路 UV',
    `diff_uv`                BIGINT                 COMMENT 'UV 差异（离线 - 实时）',
    `realtime_pv`            BIGINT                 COMMENT '实时链路 PV',
    `batch_pv`               BIGINT                 COMMENT '离线链路 PV',
    `diff_pv`                BIGINT                 COMMENT 'PV 差异',
    `realtime_view_cnt`      BIGINT                 COMMENT '实时 VIEW 次数',
    `batch_view_cnt`         BIGINT                 COMMENT '离线 VIEW 次数',
    `diff_view_cnt`          BIGINT                 COMMENT 'VIEW 差异',
    `realtime_click_cnt`     BIGINT                 COMMENT '实时 CLICK 次数',
    `batch_click_cnt`        BIGINT                 COMMENT '离线 CLICK 次数',
    `diff_click_cnt`         BIGINT                 COMMENT 'CLICK 差异',
    `realtime_cart_cnt`      BIGINT                 COMMENT '实时 CART 次数',
    `batch_cart_cnt`         BIGINT                 COMMENT '离线 CART 次数',
    `diff_cart_cnt`          BIGINT                 COMMENT 'CART 差异',
    `realtime_favorite_cnt`  BIGINT                 COMMENT '实时 FAVORITE 次数',
    `batch_favorite_cnt`     BIGINT                 COMMENT '离线 FAVORITE 次数',
    `diff_favorite_cnt`      BIGINT                 COMMENT 'FAVORITE 差异',
    `realtime_buy_cnt`       BIGINT                 COMMENT '实时 BUY 次数',
    `batch_buy_cnt`          BIGINT                 COMMENT '离线 BUY 次数',
    `diff_buy_cnt`           BIGINT                 COMMENT 'BUY 差异',
    `realtime_click_rate`    DECIMAL(10,4)          COMMENT '实时点击率（派生量，仅留证）',
    `batch_click_rate`       DECIMAL(10,4)          COMMENT '离线点击率（派生量，仅留证）',
    `diff_click_rate`        DECIMAL(10,4)          COMMENT '点击率差异',
    `realtime_cart_rate`     DECIMAL(10,4)          COMMENT '实时加购率（派生量，仅留证）',
    `batch_cart_rate`        DECIMAL(10,4)          COMMENT '离线加购率（派生量，仅留证）',
    `diff_cart_rate`         DECIMAL(10,4)          COMMENT '加购率差异',
    `realtime_buy_rate`      DECIMAL(10,4)          COMMENT '实时购买转化率（派生量，仅留证）',
    `batch_buy_rate`         DECIMAL(10,4)          COMMENT '离线购买转化率（派生量，仅留证）',
    `diff_buy_rate`          DECIMAL(10,4)          COMMENT '购买转化率差异',
    `is_match`               BOOLEAN                COMMENT '本窗口是否完全一致（判据：uv/pv/6 个行为计数）',
    `realtime_rate_anomaly`  BOOLEAN                COMMENT '实时侧比率列与自身计数矛盾的标记（数据缺陷，应为 false）',
    `batch_rate_anomaly`     BOOLEAN                COMMENT '离线侧比率列与自身计数矛盾的标记（应为 false）',
    `compared_at`            DATETIME               COMMENT '对账执行时间'
)
UNIQUE KEY(`window_start`)
DISTRIBUTED BY HASH(`window_start`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);

-- ------------------------------------------------------------
-- 批流对账汇总（流量域，追加式：保留历史批次，**不 TRUNCATE**）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lakehouse_ads.ads_reconcile_traffic_summary (
    `batch_id`              VARCHAR(64)   NOT NULL COMMENT '对账批次标识',
    `compared_at`           DATETIME               COMMENT '对账执行时间',
    `scope_start`           DATETIME               COMMENT '对账区间起点',
    `scope_end`             DATETIME               COMMENT '对账区间终点',
    `realtime_windows`      BIGINT                 COMMENT '区间内实时侧窗口数',
    `batch_windows`         BIGINT                 COMMENT '区间内离线侧窗口数',
    `realtime_only_windows` BIGINT                 COMMENT '仅实时侧有的窗口数',
    `batch_only_windows`    BIGINT                 COMMENT '仅离线侧有的窗口数',
    `matched_windows`       BIGINT                 COMMENT '一致窗口数',
    `mismatched_windows`    BIGINT                 COMMENT '不一致窗口数',
    `first_mismatch_at`     DATETIME               COMMENT '首个不一致窗口',
    `realtime_min_uv`       BIGINT                 COMMENT '实时侧窗口 UV 最小值',
    `realtime_max_uv`       BIGINT                 COMMENT '实时侧窗口 UV 最大值',
    `batch_min_uv`          BIGINT                 COMMENT '离线侧窗口 UV 最小值',
    `batch_max_uv`          BIGINT                 COMMENT '离线侧窗口 UV 最大值',
    `realtime_total_pv`     BIGINT                 COMMENT '实时侧 PV 合计',
    `batch_total_pv`        BIGINT                 COMMENT '离线侧 PV 合计',
    `realtime_rate_anomaly_windows` BIGINT         COMMENT '实时侧"比率与自身计数矛盾"的窗口数（数据缺陷计数）',
    `batch_rate_anomaly_windows`    BIGINT         COMMENT '离线侧同上的窗口数（应为 0）',
    `is_pass`               BOOLEAN                COMMENT '总体是否通过'
)
UNIQUE KEY(`batch_id`)
DISTRIBUTED BY HASH(`batch_id`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);
