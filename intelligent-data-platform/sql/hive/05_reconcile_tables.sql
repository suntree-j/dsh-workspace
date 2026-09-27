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

-- ============================================================
-- Sprint 5 — 流量域批流对账结果表（新增）
-- ============================================================
--
-- !! 为什么**新增**表，而不是给 ads_reconcile_summary 加一列 domain !!
--   两条路都可行，但代价不同：
--     加列 → 必须同时改 Sprint 3 的交易域对账 SQL、Doris DDL、
--            装载脚本的列清单与 verify-sprint-3.sh 的断言，
--            而交易域已经验收通过 —— 改它等于把已验收的东西重新拉回未验收状态。
--     新表 → 两侧互不影响，交易域的证据链保持原样（可回滚、可对照）。
--   本项目一贯的选择是后者（与"迁移期两套表并存、逐表对照"同一条原则）。
--
-- 与实时表的对应关系：
--   lakehouse.ads_reconcile_traffic_1m ↔ ecommerce.ads_realtime_traffic_1m
--
-- 幂等：EXTERNAL TABLE + INSERT OVERWRITE 分区。
-- ============================================================

CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_reconcile_traffic_1m (
    window_start             TIMESTAMP     COMMENT '窗口开始（事件时间）',
    realtime_uv              BIGINT        COMMENT '实时链路 UV（去重）',
    batch_uv                 BIGINT        COMMENT '离线链路 UV（去重）',
    diff_uv                  BIGINT        COMMENT 'UV 差异（离线 - 实时）',
    realtime_pv              BIGINT        COMMENT '实时链路 PV（可加）',
    batch_pv                 BIGINT        COMMENT '离线链路 PV（可加）',
    diff_pv                  BIGINT        COMMENT 'PV 差异',
    realtime_view_cnt        BIGINT        COMMENT '实时 VIEW 次数',
    batch_view_cnt           BIGINT        COMMENT '离线 VIEW 次数',
    diff_view_cnt            BIGINT        COMMENT 'VIEW 差异',
    realtime_click_cnt       BIGINT        COMMENT '实时 CLICK 次数',
    batch_click_cnt          BIGINT        COMMENT '离线 CLICK 次数',
    diff_click_cnt           BIGINT        COMMENT 'CLICK 差异',
    realtime_cart_cnt        BIGINT        COMMENT '实时 CART 次数',
    batch_cart_cnt           BIGINT        COMMENT '离线 CART 次数',
    diff_cart_cnt            BIGINT        COMMENT 'CART 差异',
    realtime_favorite_cnt    BIGINT        COMMENT '实时 FAVORITE 次数',
    batch_favorite_cnt       BIGINT        COMMENT '离线 FAVORITE 次数',
    diff_favorite_cnt        BIGINT        COMMENT 'FAVORITE 差异',
    realtime_buy_cnt         BIGINT        COMMENT '实时 BUY 次数',
    batch_buy_cnt            BIGINT        COMMENT '离线 BUY 次数',
    diff_buy_cnt             BIGINT        COMMENT 'BUY 差异',
    realtime_click_rate      DECIMAL(10,4) COMMENT '实时点击率（派生量，仅留证）',
    batch_click_rate         DECIMAL(10,4) COMMENT '离线点击率（派生量，仅留证）',
    diff_click_rate          DECIMAL(10,4) COMMENT '点击率差异',
    realtime_cart_rate       DECIMAL(10,4) COMMENT '实时加购率（派生量，仅留证）',
    batch_cart_rate          DECIMAL(10,4) COMMENT '离线加购率（派生量，仅留证）',
    diff_cart_rate           DECIMAL(10,4) COMMENT '加购率差异',
    realtime_buy_rate        DECIMAL(10,4) COMMENT '实时购买转化率（派生量，仅留证）',
    batch_buy_rate           DECIMAL(10,4) COMMENT '离线购买转化率（派生量，仅留证）',
    diff_buy_rate            DECIMAL(10,4) COMMENT '购买转化率差异',
    is_match                 BOOLEAN       COMMENT '本窗口是否完全一致（判据：uv/pv/6 个行为计数）',
    -- !! 这两列是"差异归谁"的取证列，Sprint 5 对账时新增 !!
    --   含义：该侧**存下来的**比率列与"由该侧自己的计数重算"的结果是否矛盾。
    --   为什么需要它：
    --     比率列不参与 is_match 判据（它是分子分母的派生量，计数相等时数学上必然相等）。
    --     但实测发现实时侧有 1 个窗口自相矛盾（view_cnt=2, click_cnt=1, click_rate=0.0000，
    --     按 metrics.md 公式应为 0.5000），而离线侧同一判据为 0。
    --     把判定落成两列，差异就有了**可查的行**，而不是靠推断谁对谁错。
    realtime_rate_anomaly    BOOLEAN       COMMENT '实时侧比率列与自身计数矛盾的标记（数据缺陷）',
    batch_rate_anomaly       BOOLEAN       COMMENT '离线侧比率列与自身计数矛盾的标记（应为 false）',
    compared_at              TIMESTAMP     COMMENT '对账执行时间'
)
COMMENT 'ADS-批流对账结果（流量域逐窗口差异留痕；is_match=false 即对账失败）'
PARTITIONED BY (dt STRING COMMENT '分区日期（窗口开始所在日）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/reconcile_traffic_1m';

-- ------------------------------------------------------------
-- 流量域对账汇总（每批次一行）
--
-- 覆盖情况为什么也要落盘：
--   交易域的 ads_reconcile_summary 只记窗口数，无法回答
--   「实时侧有 19644 个窗口，离线只对上了 19640 个，少的 4 个在哪」。
--   流量域的数据范围恰好几乎完全重合（这正是能做全窗口对账的原因），
--   因此这里额外记录两侧的时间范围与"各自独有的窗口数"，
--   使"差异为 0"这句话有边界可核 —— 而不是靠置信度。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.ads_reconcile_traffic_summary (
    batch_id             STRING        COMMENT '本次对账批次标识（= 执行时间戳）',
    compared_at          TIMESTAMP     COMMENT '对账执行时间',
    scope_start          TIMESTAMP     COMMENT '对账区间起点（含）',
    scope_end            TIMESTAMP     COMMENT '对账区间终点（不含）',
    realtime_windows     BIGINT        COMMENT '区间内实时侧窗口数',
    batch_windows        BIGINT        COMMENT '区间内离线侧窗口数',
    realtime_only_windows BIGINT       COMMENT '仅实时侧有的窗口数（离线漏算）',
    batch_only_windows   BIGINT        COMMENT '仅离线侧有的窗口数（离线多算）',
    matched_windows      BIGINT        COMMENT '完全一致的窗口数',
    mismatched_windows   BIGINT        COMMENT '存在差异的窗口数',
    first_mismatch_at    TIMESTAMP     COMMENT '第一个不一致的窗口（无则为 NULL）',
    realtime_min_uv      BIGINT        COMMENT '实时侧窗口 UV 最小值（分布核对用）',
    realtime_max_uv      BIGINT        COMMENT '实时侧窗口 UV 最大值',
    batch_min_uv         BIGINT        COMMENT '离线侧窗口 UV 最小值',
    batch_max_uv         BIGINT        COMMENT '离线侧窗口 UV 最大值',
    realtime_total_pv    BIGINT        COMMENT '实时侧 PV 合计（可加）',
    batch_total_pv       BIGINT        COMMENT '离线侧 PV 合计（可加）',
    realtime_rate_anomaly_windows BIGINT COMMENT '实时侧"比率与自身计数矛盾"的窗口数（数据缺陷计数）',
    batch_rate_anomaly_windows    BIGINT COMMENT '离线侧同上的窗口数（应为 0）',
    is_pass              BOOLEAN       COMMENT '总体是否通过（mismatched_windows = 0）'
)
COMMENT 'ADS-批流对账汇总（流量域；每批次一行）'
PARTITIONED BY (dt STRING COMMENT '分区日期（对账执行日）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/ads/reconcile_traffic_summary';
