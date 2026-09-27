-- ============================================================
-- Sprint 5 — 流量域 DWD → DWS 轻度聚合（Spark SQL）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_5.md 第 3.4 节
-- 口径：sql/metadata/metrics.md 第 3 节（唯一权威）
-- 目标表定义：sql/hive/03_dws_tables.sql
--
-- !! 本文件的核心口径约束：uv 是去重指标，跨窗口不可加 !!
--   「天 UV」必须回到明细做精确去重，**绝不能**写成 SUM(分钟 uv)。
--   本项目里这个错误有多大，有实测数字：
--     19644 个分钟窗口的 uv 之和 = 19999
--     而全部 20000 个事件的去重用户数只有 1199（实测）
--   两者差 16 倍。若从分钟表 SUM 上卷，日 UV 会系统性偏大，
--   而且偏得"看起来合理"（比 PV 小），不会有人发现。
--
--   pv 与 6 个行为计数是可加量，从分钟/明细上卷都可以；
--   本文件统一从 DWD 明细算，理由是：
--     ① DWS 的输入是 DWD（分层依赖不跨层跳）；
--     ② 从明细算 uv 与从分钟表算 pv 混在一张 INSERT 里，
--        会让"这个数到底可不可加"变得要靠记忆判断。
--
-- 幂等：按 part_dt 动态分区覆盖，重跑同一批数据结果不变。
-- ============================================================

-- ------------------------------------------------------------
-- 1. 日期骨架：出现过行为事件的日期
--
-- 为什么先建骨架：
--   与实际存在的分区对齐，保证"某天确实没有事件 → 不产行"
--   （而不是产一行全 0 的假数据）。与交易域 DWS 的做法一致。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_traffic_spine;
CREATE OR REPLACE TEMPORARY VIEW v_traffic_spine AS
SELECT DISTINCT CAST(TO_DATE(event_time) AS STRING) AS dt
FROM lakehouse.dwd_traffic_behavior_detail
WHERE event_time IS NOT NULL;

-- ------------------------------------------------------------
-- 2. 按天汇总：可加量 + 精确去重的 uv
--
-- !! uv 用 SIZE(COLLECT_SET(user_id)) 而不是 COUNT(DISTINCT user_id) !!
--   与 _common.exact_distinct 的理由相同：Spark 的 COUNT(DISTINCT x)
--   默认走 HyperLogLog 近似（约 5% 误差）。本表的 uv 要拿来做
--   「日 UV == DWD 当日精确去重」这类**相等断言**，近似值会让断言随机失败。
--   注意：ads_traffic_1m 的 uv 列**故意**保留 COUNT(DISTINCT user_id)，
--   因为那一列要跟随实时侧 Flink 的实现语义（详见 07_traffic_ads.sql）。
--   两处写法不同是**刻意的**，不是不一致。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_traffic_day;
CREATE OR REPLACE TEMPORARY VIEW v_traffic_day AS
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       COUNT(*)                            AS pv,
       SIZE(COLLECT_SET(user_id))          AS uv,
       SUM(CASE WHEN event_type = 'VIEW'     THEN 1 ELSE 0 END) AS view_cnt,
       SUM(CASE WHEN event_type = 'CLICK'    THEN 1 ELSE 0 END) AS click_cnt,
       SUM(CASE WHEN event_type = 'CART'     THEN 1 ELSE 0 END) AS cart_cnt,
       SUM(CASE WHEN event_type = 'FAVORITE' THEN 1 ELSE 0 END) AS favorite_cnt,
       SUM(CASE WHEN event_type = 'BUY'      THEN 1 ELSE 0 END) AS buy_cnt
FROM lakehouse.dwd_traffic_behavior_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING);

-- ------------------------------------------------------------
-- 3. DWS：流量总览 1 天
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.dws_traffic_overview_1d PARTITION (part_dt)
SELECT s.dt                     AS dt,
       CAST(COALESCE(d.pv, 0) AS BIGINT)           AS pv,
       CAST(COALESCE(d.uv, 0) AS BIGINT)           AS uv,
       CAST(COALESCE(d.view_cnt, 0) AS BIGINT)     AS view_cnt,
       CAST(COALESCE(d.click_cnt, 0) AS BIGINT)    AS click_cnt,
       CAST(COALESCE(d.cart_cnt, 0) AS BIGINT)     AS cart_cnt,
       CAST(COALESCE(d.favorite_cnt, 0) AS BIGINT) AS favorite_cnt,
       CAST(COALESCE(d.buy_cnt, 0) AS BIGINT)      AS buy_cnt,
       s.dt                     AS part_dt
FROM v_traffic_spine s
LEFT JOIN v_traffic_day d ON s.dt = d.dt;

-- ------------------------------------------------------------
-- 4. DWS：行为漏斗 1 天
--
-- 漏斗各步的"人数"必须回到明细去重（同一条不可加规则）。
-- 比率按 metrics.md 第 3 节的公式计算，分母 0 时 NULL。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_traffic_funnel;
CREATE OR REPLACE TEMPORARY VIEW v_traffic_funnel AS
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       SIZE(COLLECT_SET(user_id))          AS uv,
       SUM(CASE WHEN event_type = 'VIEW'     THEN 1 ELSE 0 END) AS view_cnt,
       SUM(CASE WHEN event_type = 'CLICK'    THEN 1 ELSE 0 END) AS click_cnt,
       SUM(CASE WHEN event_type = 'CART'     THEN 1 ELSE 0 END) AS cart_cnt,
       SUM(CASE WHEN event_type = 'BUY'      THEN 1 ELSE 0 END) AS buy_cnt,
       SUM(CASE WHEN event_type = 'FAVORITE' THEN 1 ELSE 0 END) AS favorite_cnt,
       SIZE(COLLECT_SET(CASE WHEN event_type = 'VIEW'  THEN user_id END)) AS view_user_cnt,
       SIZE(COLLECT_SET(CASE WHEN event_type = 'CLICK' THEN user_id END)) AS click_user_cnt,
       SIZE(COLLECT_SET(CASE WHEN event_type = 'CART'  THEN user_id END)) AS cart_user_cnt,
       SIZE(COLLECT_SET(CASE WHEN event_type = 'BUY'   THEN user_id END)) AS buy_user_cnt
FROM lakehouse.dwd_traffic_behavior_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING);

INSERT OVERWRITE TABLE lakehouse.dws_traffic_funnel_1d PARTITION (part_dt)
SELECT s.dt                                   AS dt,
       CAST(COALESCE(f.uv, 0) AS BIGINT)             AS uv,
       CAST(COALESCE(f.view_cnt, 0) AS BIGINT)       AS view_cnt,
       CAST(COALESCE(f.click_cnt, 0) AS BIGINT)      AS click_cnt,
       CAST(COALESCE(f.cart_cnt, 0) AS BIGINT)       AS cart_cnt,
       CAST(COALESCE(f.buy_cnt, 0) AS BIGINT)        AS buy_cnt,
       CAST(COALESCE(f.favorite_cnt, 0) AS BIGINT)   AS favorite_cnt,
       CAST(COALESCE(f.view_user_cnt, 0) AS BIGINT)  AS view_user_cnt,
       CAST(COALESCE(f.click_user_cnt, 0) AS BIGINT) AS click_user_cnt,
       CAST(COALESCE(f.cart_user_cnt, 0) AS BIGINT)  AS cart_user_cnt,
       CAST(COALESCE(f.buy_user_cnt, 0) AS BIGINT)   AS buy_user_cnt,
       -- 比率：先有分子分母再相除；分母为 0 时 NULL（不是 0）
       CASE WHEN COALESCE(f.view_cnt, 0) > 0
            THEN CAST(f.click_cnt / f.view_cnt AS DECIMAL(10, 4))
       END                                        AS click_rate,
       CASE WHEN COALESCE(f.click_cnt, 0) > 0
            THEN CAST(f.cart_cnt / f.click_cnt AS DECIMAL(10, 4))
       END                                        AS cart_rate,
       CASE WHEN COALESCE(f.cart_cnt, 0) > 0
            THEN CAST(f.buy_cnt / f.cart_cnt AS DECIMAL(10, 4))
       END                                        AS buy_rate,
       s.dt                                       AS part_dt
FROM v_traffic_spine s
LEFT JOIN v_traffic_funnel f ON s.dt = f.dt;
