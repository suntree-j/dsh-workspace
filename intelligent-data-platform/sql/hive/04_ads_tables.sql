-- ============================================================
-- Sprint 3 — 湖仓 ADS 层（应用层）建表
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_3.md 第 3 节；口径见 sql/metadata/metrics.md
--
-- 职责：直接面向指标与报表，**含比率指标**。
--
-- !! 本层最重要的约束：批流同形 !!
--   ads_batch_trade_1m 的字段名 / 类型 / 顺序
--   必须与实时表 ecommerce.ads_realtime_trade_1m
--   （见 sql/doris/12_ads_tables.sql）逐字段一致。
--   只有同形，才可能"同名同口径"地逐窗口对账；
--   对账逻辑见 05_reconcile_tables.sql 与 infrastructure/spark/sql/04_ads_metrics.sql。
--
-- 粒度设计（Sprint 3 的关键决定）：
--   实时 ADS 是 1 分钟粒度，若离线只出天粒度，两者根本无法逐条比对，
--   "批流对账"就只能停留在口号上。因此 ADS 同时出两套：
--     1m  —— 与实时表同形，用于对账（也用于分钟级回溯）
--     1d  —— 报表与看板用，避免看板把 11000+ 行分钟数据拉回来自己聚合
--
-- 空值约定（与 metrics.md 第 2.1 节、实时链路的 Flink 作业一致）：
--   可加指标（gmv / order_cnt / ...）无事件时为 0，不是 NULL
--   比率指标（avg_order_amount / payment_success_rate / refund_rate）分母为 0 时为 NULL
-- ============================================================

-- ------------------------------------------------------------
-- ADS：离线交易总览（1 分钟粒度，与实时表同形）
--
-- 时间轴为什么用事件时间派生（不是调度时间）：
--   实时链路的窗口 key 是事件时间 window_start，
--   离线若用处理时间，「同一分钟」对不上，对账必然全红。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_batch_trade_1m (
    window_start        TIMESTAMP     COMMENT '窗口开始（事件时间）',
    window_end          TIMESTAMP     COMMENT '窗口结束（事件时间）',
    gmv                 DECIMAL(18,2) COMMENT 'GMV：窗口内下单金额合计',
    order_cnt           BIGINT        COMMENT '订单量：窗口内订单创建事件数',
    order_user_cnt      BIGINT        COMMENT '下单用户数（去重）',
    avg_order_amount    DECIMAL(18,2) COMMENT '客单价 = gmv / order_cnt，分母为 0 时 NULL',
    payment_cnt         BIGINT        COMMENT '支付成功笔数',
    payment_amount      DECIMAL(18,2) COMMENT '支付成功金额',
    payment_fail_cnt    BIGINT        COMMENT '支付失败笔数',
    payment_success_rate DECIMAL(10,4) COMMENT '支付成功率 = 成功/(成功+失败)，分母为 0 时 NULL',
    refund_cnt          BIGINT        COMMENT '退款笔数',
    refund_amount       DECIMAL(18,2) COMMENT '退款金额',
    refund_rate         DECIMAL(10,4) COMMENT '退款率 = 退款金额/支付金额，分母为 0 时 NULL'
)
COMMENT 'ADS-离线交易总览（1 分钟，与 ads_realtime_trade_1m 同形，用于批流对账）'
PARTITIONED BY (dt STRING COMMENT '分区日期（窗口开始所在日）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/batch_trade_1m';

-- ------------------------------------------------------------
-- ADS：离线交易总览（1 天，报表 / 看板用）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_batch_trade_1d (
    dt                  STRING        COMMENT '统计日期',
    gmv                 DECIMAL(18,2) COMMENT '当日 GMV',
    order_cnt           BIGINT        COMMENT '当日订单量',
    order_user_cnt      BIGINT        COMMENT '当日下单用户数（去重）',
    avg_order_amount    DECIMAL(18,2) COMMENT '当日客单价 = gmv / order_cnt',
    payment_cnt         BIGINT        COMMENT '当日支付成功笔数',
    payment_amount      DECIMAL(18,2) COMMENT '当日支付成功金额',
    payment_fail_cnt    BIGINT        COMMENT '当日支付失败笔数',
    payment_success_rate DECIMAL(10,4) COMMENT '当日支付成功率',
    refund_cnt          BIGINT        COMMENT '当日退款笔数',
    refund_amount       DECIMAL(18,2) COMMENT '当日退款金额',
    refund_rate         DECIMAL(10,4) COMMENT '当日退款率 = 退款金额/支付金额'
)
COMMENT 'ADS-离线交易总览（按天，看板与报表）'
PARTITIONED BY (part_dt STRING COMMENT '分区日期（= dt）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/batch_trade_1d';

-- ------------------------------------------------------------
-- ADS：离线类目销售（1 分钟，与 ads_realtime_category_1m 同形）
--
-- !! 列顺序与实时表一致：window_start, category_name 在前 !!
--   （Doris 侧 UNIQUE KEY 必须是有序前缀，见 sql/doris/12_ads_tables.sql）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_batch_category_1m (
    window_start    TIMESTAMP     COMMENT '窗口开始（事件时间）',
    category_name   STRING        COMMENT '商品类目',
    window_end      TIMESTAMP     COMMENT '窗口结束（事件时间）',
    order_cnt       BIGINT        COMMENT '类目订单数',
    gmv             DECIMAL(18,2) COMMENT '类目下单金额合计',
    total_quantity  BIGINT        COMMENT '类目商品件数',
    avg_order_amount DECIMAL(18,2) COMMENT '类目客单价 = gmv / order_cnt'
)
COMMENT 'ADS-离线类目销售（1 分钟，与 ads_realtime_category_1m 同形）'
PARTITIONED BY (dt STRING COMMENT '分区日期（窗口开始所在日）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/batch_category_1m';

-- ------------------------------------------------------------
-- ADS：离线类目销售（1 天）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_batch_category_1d (
    dt              STRING        COMMENT '统计日期',
    category_name   STRING        COMMENT '商品类目',
    order_cnt       BIGINT        COMMENT '类目订单数',
    order_user_cnt  BIGINT        COMMENT '类目下单用户数（去重）',
    gmv             DECIMAL(18,2) COMMENT '类目下单金额合计',
    total_quantity  BIGINT        COMMENT '类目商品件数',
    avg_order_amount DECIMAL(18,2) COMMENT '类目客单价 = gmv / order_cnt'
)
COMMENT 'ADS-离线类目销售（按天）'
PARTITIONED BY (part_dt STRING COMMENT '分区日期（= dt）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/batch_category_1d';

-- ============================================================
-- Sprint 5 — 流量域 ADS（新增）
-- ============================================================
--
-- !! 本层存在的唯一理由：与实时链路算同一个数，并且有证据 !!
--   ads_traffic_1m ↔ ecommerce.ads_realtime_traffic_1m
--   字段名 / 类型 / 顺序逐列一致（见 sql/doris/12_ads_tables.sql），
--   对账逻辑见 05_reconcile_tables.sql 与 infrastructure/spark/sql/06_traffic_ads.sql。
--
-- !! 口径：去重指标 vs 可加指标（metrics.md 第 3 节 / SPRINT_5.md 第 3.5 节）!!
--   uv                 去重指标 —— 逐窗口可比，**窗口之间不可相加**
--   pv / view_cnt / click_cnt / cart_cnt / favorite_cnt / buy_cnt
--                      可加指标 —— 逐窗口可比，且可向上卷
--   比率（click_rate / cart_rate / buy_rate）是派生量，
--   必须"先合计分子分母再相除"，不能对逐窗口比率求平均。
--
-- 空值约定（与实时 Flink 作业一致）：
--   可加指标无事件时为 0（本表的窗口都是"有事件的窗口"，天然成立）
--   比率分母为 0 时为 NULL —— 注意实时侧实测存在 0.0000 的行
--   （例如"这一分钟只有 CLICK 没有 VIEW"时 click_rate 的分母为 0）。
--   两边都必须产生相同的 NULL/0 形态，否则对账会出现假差异。
-- ============================================================

-- ------------------------------------------------------------
-- ADS：离线流量（1 分钟粒度，与实时表同形）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_traffic_1m (
    window_start  TIMESTAMP     COMMENT '窗口开始（事件时间）',
    window_end    TIMESTAMP     COMMENT '窗口结束（事件时间）',
    uv            BIGINT        COMMENT '去重用户数（**不可加**）',
    pv            BIGINT        COMMENT '行为事件总数（可加）',
    view_cnt      BIGINT        COMMENT 'VIEW 次数（可加）',
    click_cnt     BIGINT        COMMENT 'CLICK 次数（可加）',
    cart_cnt      BIGINT        COMMENT 'CART 次数（可加）',
    favorite_cnt  BIGINT        COMMENT 'FAVORITE 次数（可加）',
    buy_cnt       BIGINT        COMMENT 'BUY 次数（可加）',
    click_rate    DECIMAL(10,4) COMMENT '点击率 = click_cnt / view_cnt，分母为 0 时 NULL',
    cart_rate     DECIMAL(10,4) COMMENT '加购率 = cart_cnt / click_cnt，分母为 0 时 NULL',
    buy_rate      DECIMAL(10,4) COMMENT '购买转化率 = buy_cnt / cart_cnt，分母为 0 时 NULL'
)
COMMENT 'ADS-离线流量（1 分钟，与 ads_realtime_traffic_1m 同形，用于批流对账）'
PARTITIONED BY (dt STRING COMMENT '分区日期（窗口开始所在日）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/traffic_1m';

-- ------------------------------------------------------------
-- ADS：离线流量（1 天，报表 / 看板用）
--
-- 上卷规则（**与交易域 1d 表同一条规则**）：
--   可加量直接从 1m 表 SUM —— 保证「天 = 该天所有分钟之和」恒成立；
--   uv 回到明细精确去重 —— 去重指标不可加，SUM(uv) 会重复计数。
--   比率从"上卷后的可加量"重算，而不是对分钟比率求平均。
--
-- 为什么 uv 用 TO_DATE(window_start) 分组而不是 TO_DATE(event_time)：
--   dt 分区列就是 TO_DATE(window_start)，按窗口起点分组才能保证
--   1d 表按 dt 与 1m 表按 dt 的分组键**完全相同**；
--   用 event_time 分组在跨日分钟上会与分区归属不一致（虽然本数据集罕见）。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_traffic_1d (
    dt            STRING        COMMENT '统计日期',
    uv            BIGINT        COMMENT '当日去重用户数（**不可加**）',
    pv            BIGINT        COMMENT '当日行为事件总数（可加）',
    view_cnt      BIGINT        COMMENT 'VIEW 次数（可加）',
    click_cnt     BIGINT        COMMENT 'CLICK 次数（可加）',
    cart_cnt      BIGINT        COMMENT 'CART 次数（可加）',
    favorite_cnt  BIGINT        COMMENT 'FAVORITE 次数（可加）',
    buy_cnt       BIGINT        COMMENT 'BUY 次数（可加）',
    click_rate    DECIMAL(10,4) COMMENT '点击率 = click_cnt / view_cnt，分母为 0 时 NULL',
    cart_rate     DECIMAL(10,4) COMMENT '加购率 = cart_cnt / click_cnt，分母为 0 时 NULL',
    buy_rate      DECIMAL(10,4) COMMENT '购买转化率 = buy_cnt / cart_cnt，分母为 0 时 NULL'
)
COMMENT 'ADS-离线流量（按天，看板与报表）'
PARTITIONED BY (part_dt STRING COMMENT '分区日期（= dt）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/traffic_1d';
