-- ============================================================
-- Apache Doris 初始化
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- Sprint：0
-- 依据：docs/sprint/SPRINT_0.md 第 11 节
--
-- 执行机制（重要）：
--   Doris BE 镜像的 entry_point.sh 会自动执行容器内
--   /docker-entrypoint-initdb.d/ 下的 *.sql / *.sh，
--   执行方式为： mysql -uroot -h <MASTER_FE_IP> -P 9030 < <file>
--   该目录由 docker-compose.yml 从 ./sql/doris 挂载而来。
--
--   因此本文件：
--     1) 由 BE 容器初始化机制自动执行，无需手工操作；
--     2) 执行时未指定默认库，必须显式 USE ecommerce；
--     3) 全部语句幂等，容器重建不会报错。
--
-- 说明：Sprint 0 只建立 ecommerce 库与 test_connection 测试表，
--       不提前创建 ODS/DWD/DWS/ADS 分层。
--
-- 关于 BE 注册：
--   BE 无需在此手工注册。BE 容器入口脚本会自行执行
--   ALTER SYSTEM ADD BACKEND '<BE_ADDR>'，见
--   docs/development-environment.md 第 4.5 节。
-- ============================================================

-- ------------------------------------------------------------
-- 1. 业务库
-- ------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS `ecommerce`;

-- ------------------------------------------------------------
-- 2. 单副本默认值
--
-- 关键：Doris 建表默认 replication_num = 3，而 Sprint 0 只有 1 个 BE，
--       若不修改默认值，任何不带 PROPERTIES 的建表都会失败。
--       SPRINT_0.md 第 11 节亦明确要求 replication_num = 1。
--       此处设置全局默认值，使后续 Sprint 建表无需重复声明。
-- ------------------------------------------------------------
ALTER SYSTEM SET default_replication_num = 1;

USE `ecommerce`;

-- ------------------------------------------------------------
-- 3. 连通性测试表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `test_connection` (
    `id`          BIGINT,
    `message`     VARCHAR(255),
    `create_time` DATETIME
)
DUPLICATE KEY(`id`)
DISTRIBUTED BY HASH(`id`) BUCKETS 1
PROPERTIES (
    "replication_num" = "1"
);

-- ------------------------------------------------------------
-- 4. 测试数据（幂等：避免容器重建后重复插入）
-- ------------------------------------------------------------
DELETE FROM `test_connection` WHERE `id` = 1;

INSERT INTO `test_connection` VALUES
    (1, 'doris connection ok', NOW());

-- ------------------------------------------------------------
-- 5. 校验：查询结果会出现在 BE 容器日志中
-- ------------------------------------------------------------
SELECT * FROM `test_connection`;
