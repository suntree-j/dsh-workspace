-- ============================================================
-- Sprint 5 — 流量域 ODS → DWD 清洗作业（Spark SQL）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_5.md 第 3.4 节
-- 口径：sql/metadata/metrics.md 第 3 节（唯一权威，本文件不得另立口径）
-- 目标表定义：sql/hive/02_dwd_tables.sql
--
-- 本文件只做 DWD 层该做的四件事：去重、清洗、维度补全、统一命名。
-- **不做聚合**（聚合属于 DWS / ADS）。
--
-- !! 与实时侧 dwd_traffic_behavior_detail 的关系 !!
--   字段名与语义逐列对齐（见 sql/doris/10_dwd_tables.sql），
--   但**加工深度不同**，这是有意的：
--     实时侧只有事件流，product 维表是通过 lookup join 补的，
--       实测 category_name 在生产表里为空（跨源 join 的固有代价）；
--     离线侧有 dwd_product_detail 维表可用，能真正补全 category_name。
--   因此 category_name **不参与对账**（对账在 ADS 层只比
--   uv / pv / 6 个行为计数 / 3 个比率 —— 这些都不依赖它）。
--   把这一点写在这里，是为了避免后来者看到"两边某一列不一样"就误以为数据错了。
--
-- 幂等：按 dt 动态分区覆盖（spark.sql.sources.partitionOverwriteMode=dynamic
--   由作业显式设置，否则 INSERT OVERWRITE 会把整表清空）。
-- ============================================================

-- ------------------------------------------------------------
-- 1. 维度：商品类目（取 DWD 维表，不直接 join ODS）
--
-- !! 为什么维表取 DWD 而不是 ODS !!
--   与交易域 01_dwd_build.sql 同一条理由：DWD 维表已经去过重。
--   直接 join ODS，一个 product_id 有多行时会把行为事件**放大**成多行，
--   而本数据集里 dwd_product_detail 的 product_id 实测唯一（600/600），
--   所以这是"防未来分叉"的写法，不是当前必须的兜底。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_traffic_product_dim;
CREATE OR REPLACE TEMPORARY VIEW v_traffic_product_dim AS
SELECT product_id, MAX(category_name) AS category_name
FROM lakehouse.dwd_product_detail
WHERE product_id IS NOT NULL
GROUP BY product_id;

-- ------------------------------------------------------------
-- 2. 行为事件：去重 + 清洗
--
-- 事件类型白名单：只保留**进入漏斗**的 5 类。
--   为什么要有白名单（而不是把 event_type 原样带过去）：
--     Kafka 事件信封里 SEARCH 也是合法类型（见 sql/metadata/kafka_topics.md）。
--     实时侧的 DWD **只落这 5 类**（实测分布：VIEW/CLICK/CART/FAVORITE/BUY，
--     合计 20000 = 事件总数）。离线若不加白名单，将来补了 SEARCH 事件后
--     就会出现"实时没有、离线有"的单边差异，
--     而对账报出来的现象是"离线多算"——根因却在一个枚举值上，很难查。
--
-- 去重键：event_id。
--   归档是"从 earliest 读到 latest"的全量重读（见 archive_behavior_events.py），
--   重跑会把同一批消息再取一遍。ODS 按 dt 分区覆盖使重跑幂等，
--   但"同一 event_id 落进两个不同 dt 分区"这种跨分区重复
--   （事件时间被改写、或归档期间跨天提交）仍可能在 DWD 层留下两行。
--   在 DWD 去重是对账能否成立的前提：多一行 PV 就对不上。
--   保留策略：event_time 最新的一行（与交易域"保留最新快照"一致）。
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_behavior_clean;
CREATE OR REPLACE TEMPORARY VIEW v_behavior_clean AS
SELECT e.event_id,
       e.event_type,
       e.user_id,
       e.product_id,
       e.device,
       e.province,
       e.event_time,
       CAST(TO_DATE(e.event_time) AS STRING) AS dt
FROM (
    SELECT b.*,
           ROW_NUMBER() OVER (
               PARTITION BY b.event_id
               ORDER BY b.event_time DESC
           ) AS rn
    FROM lakehouse.ods_behavior_event b
    -- 归档阶段的解析失败会留下 event_id 为空的行；空键无法去重，必须剔除
    WHERE b.event_id IS NOT NULL
) e
WHERE e.rn = 1
  AND e.event_type IN ('VIEW', 'CLICK', 'CART', 'FAVORITE', 'BUY')
  -- 事件时间是窗口键，为空会让该行在聚合时静默消失（不是报错，是少数据）
  AND e.event_time IS NOT NULL
  AND e.event_id IS NOT NULL
  AND e.user_id IS NOT NULL;

-- ------------------------------------------------------------
-- 3. 写入 DWD
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE lakehouse.dwd_traffic_behavior_detail PARTITION (dt)
SELECT c.event_id,
       c.event_type,
       c.user_id,
       c.product_id,
       p.category_name,
       c.device,
       c.province,
       c.event_time,
       c.dt
FROM v_behavior_clean c
LEFT JOIN v_traffic_product_dim p ON c.product_id = p.product_id;
