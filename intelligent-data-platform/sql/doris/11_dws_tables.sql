-- ============================================================
-- Sprint 1 — DWS 汇总层建表
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- 依据：docs/sprint/SPRINT_1.md 第 3.3 节
--
-- 数据来源：Flink 1 分钟滚动窗口聚合结果 → Kafka → Routine Load
--
-- !! 设计说明：为什么只有一张 DWS 表 !!
--   DWS 的职责是「按主题轻度聚合，供 ADS 复用」。
--   本 Sprint 的 ADS 指标（交易总览、流量、类目销售）粒度与 DWS
--   高度重合：若同时建 dws_trade_category_1m 与
--   ads_realtime_category_1m，两者只是字段相同的一张表，
--   属于「为分层而分层」，会增加维护成本与口径分叉风险。
--   因此 Sprint 1 只保留 Traffic 域这一张 DWS 表：
--     - dws_traffic_overview_1m 是流量的基础汇总，
--       既可直接查询，也是 ads_realtime_traffic_1m 的数据基础；
--   交易域的聚合直接由 Flink 一次算出，写入 ADS。
--   Sprint 3 建立离线分层时，会按统一口径补齐完整的 DWS 层，
--   届时两者的字段与语义必须与本文件保持一致。
--
-- 窗口约定：
--   window_start / window_end 为**事件时间**的滚动窗口边界（1 分钟），
--   由 Flink TUMBLE(event_time, INTERVAL '1' MINUTE) 产生。
--
-- 模型选择：
--   使用 UNIQUE KEY(window_start)，保证同一窗口重复写入时覆盖，
--   避免 Flink 重启重放造成指标翻倍。
-- ============================================================

CREATE DATABASE IF NOT EXISTS ecommerce;
USE ecommerce;

-- ------------------------------------------------------------
-- 流量总览 1 分钟汇总
-- 粒度：一个窗口（全局）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dws_traffic_overview_1m (
    `window_start`  DATETIME     NOT NULL COMMENT '窗口开始（事件时间）',
    `window_end`    DATETIME     NOT NULL COMMENT '窗口结束（事件时间）',
    `pv`            BIGINT                COMMENT '行为事件总数',
    `uv`            BIGINT                COMMENT '去重用户数',
    `view_cnt`      BIGINT                COMMENT 'VIEW 次数',
    `click_cnt`     BIGINT                COMMENT 'CLICK 次数',
    `cart_cnt`      BIGINT                COMMENT 'CART 次数',
    `favorite_cnt`  BIGINT                COMMENT 'FAVORITE 次数',
    `buy_cnt`       BIGINT                COMMENT 'BUY 次数'
)
UNIQUE KEY(`window_start`)
DISTRIBUTED BY HASH(`window_start`) BUCKETS 3
PROPERTIES (
    "replication_num" = "1",
    "enable_unique_key_merge_on_write" = "true"
);
