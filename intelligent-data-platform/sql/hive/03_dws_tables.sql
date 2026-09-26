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
