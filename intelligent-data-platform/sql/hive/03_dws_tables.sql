-- ============================================================
-- Sprint 3 — 湖仓 DWS 层（汇总层）建表
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_3.md 第 3 节
--
-- 职责：按主题做**天粒度**轻度聚合，只出「可加指标」。
--
-- !! 为什么 DWS 不出比率指标（avg_order_amount / payment_success_rate ...）!!
--   比率的分子与分母来自不同的聚合结果。如果让 DWS 各自算比率，
--   就会出现「两个 DWS 表各算一半口径」的分裂风险，
--   与本项目「同一指标只能有一个定义」（sql/metadata/metrics.md）
--   的约定冲突。
--   因此：DWS 只汇总可加量（笔数 / 金额 / 件数），
--         比率一律在 ADS 层按 metrics.md 的公式统一计算。
--
-- 存储：s3a://lakehouse/warehouse/dws/<表名>/dt=<YYYY-MM-DD>/ （Parquet，按天分区）
-- 幂等：全部 IF NOT EXISTS；数据由 INSERT OVERWRITE 分区写入。
-- ============================================================

-- ------------------------------------------------------------
-- DWS：交易总览 1 天（主题：交易）
--
-- 为什么订单与支付/退款要在一个表里：
--   实时 ADS 的 ads_realtime_trade_1m 就是「同一窗口内订单+支付+退款」的宽表，
--   离线 DWS 保持同形，ADS 层才能用同一套公式复算，
--   否则 ADS 要自己去 join 三张 DWS 表，口径容易走偏。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dws_trade_overview_1d (
    dt                  STRING        COMMENT '统计日期',
    order_cnt           BIGINT        COMMENT '订单量（可加）',
    order_user_cnt      BIGINT        COMMENT '下单用户数（去重）',
    gmv                 DECIMAL(18,2) COMMENT 'GMV：下单金额合计（可加）',
    total_quantity      BIGINT        COMMENT '下单件数（可加）',
    payment_cnt         BIGINT        COMMENT '支付成功笔数（可加）',
    payment_amount      DECIMAL(18,2) COMMENT '支付成功金额（可加）',
    payment_fail_cnt    BIGINT        COMMENT '支付失败笔数（可加）',
    refund_cnt          BIGINT        COMMENT '退款笔数（可加）',
    refund_amount       DECIMAL(18,2) COMMENT '退款金额（可加）'
)
COMMENT 'DWS-交易总览（按天，只含可加指标）'
PARTITIONED BY (part_dt STRING COMMENT '分区日期（= dt，离线调度按天覆盖）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dws/trade_overview_1d';

-- ------------------------------------------------------------
-- DWS：类目销售 1 天（主题：交易 × 商品类目）
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dws_trade_category_1d (
    dt              STRING        COMMENT '统计日期',
    category_name   STRING        COMMENT '商品类目',
    order_cnt       BIGINT        COMMENT '类目订单量（可加）',
    order_user_cnt  BIGINT        COMMENT '类目下单用户数（去重）',
    gmv             DECIMAL(18,2) COMMENT '类目 GMV（可加）',
    total_quantity  BIGINT        COMMENT '类目下单件数（可加）'
)
COMMENT 'DWS-类目销售（按天 × 类目，只含可加指标）'
PARTITIONED BY (part_dt STRING COMMENT '分区日期（= dt，离线调度按天覆盖）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dws/trade_category_1d';

-- ------------------------------------------------------------
-- DWS：用户交易 1 天（主题：交易 × 用户）
--
-- 维度取 DWD 已补全后的 user_level / province，
-- 保证「用户维度下钻」在离线侧也只依赖 DWD，不再回查源库。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dws_trade_user_1d (
    dt              STRING        COMMENT '统计日期',
    user_id         BIGINT        COMMENT '用户ID',
    user_level      STRING        COMMENT '会员等级（来自 DWD 补全）',
    province        STRING        COMMENT '省份（来自 DWD 补全）',
    order_cnt       BIGINT        COMMENT '该用户当日订单量（可加）',
    gmv             DECIMAL(18,2) COMMENT '该用户当日 GMV（可加）',
    total_quantity  BIGINT        COMMENT '该用户当日下单件数（可加）'
)
COMMENT 'DWS-用户交易（按天 × 用户，只含可加指标）'
PARTITIONED BY (part_dt STRING COMMENT '分区日期（= dt，离线调度按天覆盖）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dws/trade_user_1d';

-- ============================================================
-- Sprint 5 — 流量域 DWS（新增）
-- ============================================================
--
-- !! 口径以 sql/metadata/metrics.md 第 3 节为唯一权威 !!
--   uv / pv / view_cnt / click_cnt / cart_cnt / favorite_cnt / buy_cnt
--   全部沿用那里的定义，本文件不得另立口径。
--
-- !! 本层的核心口径约束：uv 是**去重指标**，跨窗口不可加 !!
--   「天 UV」必须回到明细做 COUNT(DISTINCT user_id)，
--   绝不能写成 SUM(分钟 uv) —— 同一用户在同一分钟的多个窗口出现时会被重复计数。
--   实测证据在本项目里是可查的：19644 个分钟窗口的 uv 之和是 19999，
--   而按天去重的真实用户数远小于这个数。
--   因此：pv 与各行为计数（可加）可以从分钟表上卷，
--         uv（去重）必须从 DWD 明细重算。
--   这与交易域 dws_trade_overview_1d 里 order_user_cnt 的处理是同一条规则。
-- ============================================================

-- ------------------------------------------------------------
-- DWS：流量总览 1 天（主题：流量）
--
-- 与实时侧 dws_traffic_overview_1m 同口径、同字段名（只差时间粒度）：
--   实时   window_start 窗口 + pv/uv/各行为计数
--   离线   dt 天        + pv/uv/各行为计数
--   字段顺序也保持一致（pv 在 uv 之前），便于逐列对照。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dws_traffic_overview_1d (
    dt            STRING  COMMENT '统计日期',
    pv            BIGINT  COMMENT '行为事件总数（可加，口径见 metrics.md 第 3 节）',
    uv            BIGINT  COMMENT '去重用户数（**不可加**：跨窗口/跨天不能相加）',
    view_cnt      BIGINT  COMMENT 'VIEW 次数（可加）',
    click_cnt     BIGINT  COMMENT 'CLICK 次数（可加）',
    cart_cnt      BIGINT  COMMENT 'CART 次数（可加）',
    favorite_cnt  BIGINT  COMMENT 'FAVORITE 次数（可加）',
    buy_cnt       BIGINT  COMMENT 'BUY 次数（可加）'
)
COMMENT 'DWS-流量总览（按天；与 dws_traffic_overview_1m 同口径）'
PARTITIONED BY (part_dt STRING COMMENT '分区日期（= dt，离线调度按天覆盖）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dws/traffic_overview_1d';

-- ------------------------------------------------------------
-- DWS：行为漏斗 1 天（主题：流量 × 漏斗）
--
-- !! 为什么还要一张漏斗表（SPRINT_5.md 第 3.4 节的设计）!!
--   「漏斗逐级收窄」是本项目的一条数据正确性断言（AGENTS.md 8.2），
--   而收窄关系只有在「同一维度上逐级可比」时才能被断言：
--   总览表把 5 个计数平铺成列，收窄关系要靠人眼比；
--   漏斗表把 VIEW→CLICK→CART→BUY 排成阶梯，收窄关系是**结构性**的。
--
-- !! 去重列与实时侧的差别，必须写明 !!
--   实时侧 dws_traffic_overview_1m **只有** uv（窗口内去重用户数），
--   没有"每类行为的去重人数"。因此本表的 *_user_cnt 是**离线新增的下钻维度**，
--   它**不参与**批流对账（对账只比 ads_traffic_1m 的 **7 个判据列**：
--   uv / pv / 5 个行为计数；比率列只是派生诊断量，也不进判据）。
--   加它们的理由是它们真正有用（漏斗每步的真实人数），
--   而不是为了"看起来更完整"。
--
--   uv 语义与总览表一致：当天所有行为事件的去重用户数。
--   尖峰指标（如 click_user_cnt / view_user_cnt）用于判断"次数收窄"是不是
--   由少数用户重复行为造成的 —— 只看次数会得出误导性结论。
-- ------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS lakehouse.dws_traffic_funnel_1d (
    dt              STRING  COMMENT '统计日期',
    uv              BIGINT  COMMENT '当日去重用户数（= 总览表 uv，**不可加**）',
    view_cnt        BIGINT  COMMENT 'VIEW 事件数（可加）',
    click_cnt       BIGINT  COMMENT 'CLICK 事件数（可加）',
    cart_cnt        BIGINT  COMMENT 'CART 事件数（可加）',
    buy_cnt         BIGINT  COMMENT 'BUY 事件数（可加）',
    favorite_cnt    BIGINT  COMMENT 'FAVORITE 事件数（可加；旁支，不在主漏斗链上）',
    view_user_cnt   BIGINT  COMMENT '发生 VIEW 的去重用户数（离线新增下钻维度，不参与对账）',
    click_user_cnt  BIGINT  COMMENT '发生 CLICK 的去重用户数（同上）',
    cart_user_cnt   BIGINT  COMMENT '发生 CART 的去重用户数（同上）',
    buy_user_cnt    BIGINT  COMMENT '发生 BUY 的去重用户数（同上）',
    click_rate      DECIMAL(10,4) COMMENT '点击率 = click_cnt / view_cnt，分母为 0 时 NULL',
    cart_rate       DECIMAL(10,4) COMMENT '加购率 = cart_cnt / click_cnt，分母为 0 时 NULL',
    buy_rate        DECIMAL(10,4) COMMENT '购买转化率 = buy_cnt / cart_cnt，分母为 0 时 NULL'
)
COMMENT 'DWS-行为漏斗（按天；VIEW→CLICK→CART→BUY 阶梯）'
PARTITIONED BY (part_dt STRING COMMENT '分区日期（= dt，离线调度按天覆盖）')
STORED AS PARQUET
LOCATION 's3a://lakehouse/warehouse/dws/traffic_funnel_1d';
