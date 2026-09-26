-- ============================================================
-- Sprint 3 — 批流对账结果表（ADS 层的治理型表）
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_3.md 第 3.3 节
--
-- 为什么要有这张表：
--   "实时与离线对账通过"如果只是脚本里的一个布尔值，
--   出现分歧时无法回溯到底是哪一分钟、哪个指标错了。
--   本表把**每个窗口的双方取值与差异**都落盘，
--   使对账结论可复核、可留档、可进论文附录。
--
-- 与实时表的对应关系（对账是在两侧同名表之间做的）：
--   lakehouse.ads_batch_trade_1m  ↔  ecommerce.ads_realtime_trade_1m
--
-- 幂等：EXTERNAL TABLE + INSERT OVERWRITE 分区，可重复执行。
-- ============================================================

CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_reconcile_trade_1m (
    window_start              TIMESTAMP     COMMENT '窗口开始（事件时间）',
    realtime_gmv              DECIMAL(18,2) COMMENT '实时链路 GMV',
    batch_gmv                 DECIMAL(18,2) COMMENT '离线链路 GMV',
    diff_gmv                  DECIMAL(18,2) COMMENT 'GMV 差异（离线 - 实时）',
    realtime_order_cnt        BIGINT        COMMENT '实时订单量',
    batch_order_cnt           BIGINT        COMMENT '离线订单量',
    diff_order_cnt            BIGINT        COMMENT '订单量差异',
    realtime_order_user_cnt   BIGINT        COMMENT '实时下单用户数',
    batch_order_user_cnt      BIGINT        COMMENT '离线下单用户数',
    diff_order_user_cnt       BIGINT        COMMENT '下单用户数差异',
    realtime_payment_cnt      BIGINT        COMMENT '实时支付成功笔数',
    batch_payment_cnt         BIGINT        COMMENT '离线支付成功笔数',
    diff_payment_cnt          BIGINT        COMMENT '支付笔数差异',
    realtime_payment_amount   DECIMAL(18,2) COMMENT '实时支付金额',
    batch_payment_amount      DECIMAL(18,2) COMMENT '离线支付金额',
    diff_payment_amount       DECIMAL(18,2) COMMENT '支付金额差异',
    realtime_payment_fail_cnt BIGINT        COMMENT '实时支付失败笔数',
    batch_payment_fail_cnt    BIGINT        COMMENT '离线支付失败笔数',
    diff_payment_fail_cnt     BIGINT        COMMENT '支付失败笔数差异',
    realtime_refund_cnt       BIGINT        COMMENT '实时退款笔数',
    batch_refund_cnt          BIGINT        COMMENT '离线退款笔数',
    diff_refund_cnt           BIGINT        COMMENT '退款笔数差异',
    realtime_refund_amount    DECIMAL(18,2) COMMENT '实时退款金额',
    batch_refund_amount       DECIMAL(18,2) COMMENT '离线退款金额',
    diff_refund_amount        DECIMAL(18,2) COMMENT '退款金额差异',
    is_match                  BOOLEAN       COMMENT '本窗口是否完全一致（全部差异为 0）',
    compared_at               TIMESTAMP     COMMENT '对账执行时间'
)
COMMENT 'ADS-批流对账结果（逐窗口差异留痕；is_match=false 即对账失败）'
PARTITIONED BY (dt STRING COMMENT '分区日期（窗口开始所在日）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/reconcile_trade_1m';

-- ------------------------------------------------------------
-- 对账汇总表：每个批次的总体结论
--
-- 为什么还要一张汇总表：
--   逐窗口表有上万行，验收脚本与文档需要"一句话结论"：
--   对账了多少个窗口、错了几个、第一个错在什么时候。
--   把它也落盘，避免每次都要重新扫全表。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_reconcile_summary (
    batch_id            STRING        COMMENT '本次对账批次标识（= 执行时间戳）',
    compared_at         TIMESTAMP     COMMENT '对账执行时间',
    scope_start         TIMESTAMP     COMMENT '对账区间起点（含）',
    scope_end           TIMESTAMP     COMMENT '对账区间终点（不含）',
    realtime_windows    BIGINT        COMMENT '实时侧窗口数',
    batch_windows       BIGINT        COMMENT '离线侧窗口数',
    matched_windows     BIGINT        COMMENT '完全一致的窗口数',
    mismatched_windows  BIGINT        COMMENT '存在差异的窗口数',
    first_mismatch_at   TIMESTAMP     COMMENT '第一个不一致的窗口（无则为 NULL）',
    realtime_total_gmv  DECIMAL(18,2) COMMENT '实时侧 GMV 合计',
    batch_total_gmv     DECIMAL(18,2) COMMENT '离线侧 GMV 合计',
    is_pass             BOOLEAN       COMMENT '总体是否通过（mismatched_windows = 0）'
)
COMMENT 'ADS-批流对账汇总（每批次一行，给验收脚本与看板用）'
PARTITIONED BY (dt STRING COMMENT '分区日期（对账执行日）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/reconcile_summary';
