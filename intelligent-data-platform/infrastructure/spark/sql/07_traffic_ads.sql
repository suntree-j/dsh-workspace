-- ============================================================
-- Sprint 5 — 流量域 DWD → ADS 指标作业（Spark SQL）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_5.md 第 3.4 / 3.5 节
-- 口径：sql/metadata/metrics.md 第 3 节（唯一权威，本文件不得另立口径）
-- 目标表定义：sql/hive/04_ads_tables.sql
--
-- !! 本文件的核心要求：与实时链路的 Flink 作业逐公式同构 !!
--   实时侧公式见 infrastructure/flink/sql/05_metric_jobs.sql 的
--   sink_ads_realtime_traffic_1m（第 167~191 行）。
--   离线侧每一个指标都必须是那边那一条公式的**逐字翻译**，
--   不允许"顺手优化"。任何改写都可能改变空值与取整行为，对账就会失败。
--
-- 两边必须保持一致的四个实现细节：
--   1. 窗口 = TUMBLE 1 分钟 → DATE_TRUNC('minute', event_time)，左闭右开；
--   2. uv 保留 COUNT(DISTINCT user_id)（**故意**用近似去重）——
--      实时侧的 COUNT(DISTINCT user_id) 就是近似实现，
--      uv 是参与对账的指标，离线换成精确去重会因"算法不同"而对不上，
--      而且失败原因会变得极难判断。这条与交易域 order_user_cnt 的处理相同。
--      与之相对：**用来做相等断言的去重**（DWS 的 uv、作业自检里的天数）
--      必须用精确实现（见 _common.exact_distinct 的说明）。
--   3. 可加指标无事件时为 0（COALESCE(..., 0)），不是 NULL；
--   4. 比率指标分母为 0 时为 NULL（CASE WHEN 分母 > 0）。
--
-- !! 去重指标 vs 可加指标（metrics.md 第 3 节 / SPRINT_5.md 第 3.5 节）!!
--   uv                          去重：逐窗口可比，**窗口之间不可相加**
--   pv / 5 个行为计数（view/click/cart/favorite/buy）
--                               可加：逐窗口可比，可上卷（共 6 个可加量）
--   3 个比率                     派生：必须先合计分子分母，不能对比率求平均
--   所以本文件的 1d 表：
--     - 可加量从 1m 表 SUM（保证「天 = 该天所有分钟之和」恒成立）
--     - uv 回到 DWD 精确去重（SUM(uv) 会重复计数）
--     - 比率从"上卷后的可加量"重算
-- ============================================================

-- ------------------------------------------------------------
-- 1. ADS：离线流量（1 分钟粒度，与实时表同形）
--
-- 与实时侧同构关系（Flink 的一个 TUMBLE + GROUP BY window_start）：
--   离线这里只需要一次 GROUP BY —— 与交易域不同，流量域的 6 个可加量
--   和 uv 全部来自**同一条**行为事件流（实时侧也是同一个 src_behavior_event），
--   不需要 FULL OUTER JOIN 对齐多条流。
--   即：不存在"某窗口只有支付没有订单"那种错位，只有"有事件的窗口才出行"。
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.ads_traffic_1m PARTITION (dt)
SELECT DATE_TRUNC('minute', event_time)                    AS window_start,
       DATE_TRUNC('minute', event_time) + INTERVAL 1 MINUTE AS window_end,
       CAST(COUNT(DISTINCT user_id) AS BIGINT)             AS uv,
       CAST(COUNT(*) AS BIGINT)                            AS pv,
       CAST(SUM(CASE WHEN event_type = 'VIEW'     THEN 1 ELSE 0 END) AS BIGINT) AS view_cnt,
       CAST(SUM(CASE WHEN event_type = 'CLICK'    THEN 1 ELSE 0 END) AS BIGINT) AS click_cnt,
       CAST(SUM(CASE WHEN event_type = 'CART'     THEN 1 ELSE 0 END) AS BIGINT) AS cart_cnt,
       CAST(SUM(CASE WHEN event_type = 'FAVORITE' THEN 1 ELSE 0 END) AS BIGINT) AS favorite_cnt,
       CAST(SUM(CASE WHEN event_type = 'BUY'      THEN 1 ELSE 0 END) AS BIGINT) AS buy_cnt,
       -- 比率：与实时侧一样，分母为 0 时 NULL（不是 0）
       CASE WHEN SUM(CASE WHEN event_type = 'VIEW' THEN 1 ELSE 0 END) > 0
            THEN CAST(SUM(CASE WHEN event_type = 'CLICK' THEN 1 ELSE 0 END)
                      / SUM(CASE WHEN event_type = 'VIEW' THEN 1 ELSE 0 END)
                      AS DECIMAL(10, 4))
       END                                                 AS click_rate,
       CASE WHEN SUM(CASE WHEN event_type = 'CLICK' THEN 1 ELSE 0 END) > 0
            THEN CAST(SUM(CASE WHEN event_type = 'CART' THEN 1 ELSE 0 END)
                      / SUM(CASE WHEN event_type = 'CLICK' THEN 1 ELSE 0 END)
                      AS DECIMAL(10, 4))
       END                                                 AS cart_rate,
       CASE WHEN SUM(CASE WHEN event_type = 'CART' THEN 1 ELSE 0 END) > 0
            THEN CAST(SUM(CASE WHEN event_type = 'BUY' THEN 1 ELSE 0 END)
                      / SUM(CASE WHEN event_type = 'CART' THEN 1 ELSE 0 END)
                      AS DECIMAL(10, 4))
       END                                                 AS buy_rate,
       CAST(TO_DATE(event_time) AS STRING)                 AS dt
FROM lakehouse.dwd_traffic_behavior_detail
GROUP BY DATE_TRUNC('minute', event_time),
         CAST(TO_DATE(event_time) AS STRING);

-- ------------------------------------------------------------
-- 2. ADS：离线流量（1 天）
--
-- 上卷规则见文件头第 4 条说明。这里的 uv 用**精确**去重：
-- 它是"当日去重用户数"这个业务事实，不是对账列，
-- 而且作业自检要拿它与 DWD 当日精确去重做相等断言。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_traffic_1d_sum;
CREATE OR REPLACE TEMPORARY VIEW v_traffic_1d_sum AS
SELECT dt,
       SUM(pv)           AS pv,
       SUM(view_cnt)     AS view_cnt,
       SUM(click_cnt)    AS click_cnt,
       SUM(cart_cnt)     AS cart_cnt,
       SUM(favorite_cnt) AS favorite_cnt,
       SUM(buy_cnt)      AS buy_cnt
FROM lakehouse.ads_traffic_1m
GROUP BY dt;

DROP VIEW IF EXISTS v_traffic_1d_uv;
CREATE OR REPLACE TEMPORARY VIEW v_traffic_1d_uv AS
SELECT CAST(TO_DATE(event_time) AS STRING) AS dt,
       SIZE(COLLECT_SET(user_id))          AS uv
FROM lakehouse.dwd_traffic_behavior_detail
GROUP BY CAST(TO_DATE(event_time) AS STRING);

INSERT OVERWRITE TABLE lakehouse.ads_traffic_1d PARTITION (part_dt)
SELECT s.dt                                                     AS dt,
       CAST(COALESCE(u.uv, 0) AS BIGINT)                        AS uv,
       CAST(s.pv AS BIGINT)                                     AS pv,
       CAST(s.view_cnt AS BIGINT)                               AS view_cnt,
       CAST(s.click_cnt AS BIGINT)                              AS click_cnt,
       CAST(s.cart_cnt AS BIGINT)                               AS cart_cnt,
       CAST(s.favorite_cnt AS BIGINT)                           AS favorite_cnt,
       CAST(s.buy_cnt AS BIGINT)                                AS buy_cnt,
       CASE WHEN s.view_cnt > 0
            THEN CAST(s.click_cnt / s.view_cnt AS DECIMAL(10, 4))
       END                                                      AS click_rate,
       CASE WHEN s.click_cnt > 0
            THEN CAST(s.cart_cnt / s.click_cnt AS DECIMAL(10, 4))
       END                                                      AS cart_rate,
       CASE WHEN s.cart_cnt > 0
            THEN CAST(s.buy_cnt / s.cart_cnt AS DECIMAL(10, 4))
       END                                                      AS buy_rate,
       s.dt                                                     AS part_dt
FROM v_traffic_1d_sum s
LEFT JOIN v_traffic_1d_uv u ON s.dt = u.dt;
