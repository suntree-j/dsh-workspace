-- ============================================================
-- Apache Doris 初始化
-- ============================================================
--
-- 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
-- Sprint：0
-- 依据：docs/sprint/SPRINT_0.md 第 11 节
--
-- 执行机制（重要）：
--   由 Doris BE 容器启动时的初始化流程执行本文件，
--   执行方式为： mysql -h <FE_HOST> -P 9030 -uroot < <file>
--   该目录由 docker-compose.yml 从 ./sql/doris 挂载到
--   /docker-entrypoint-initdb.d。
--
--   !! 容器每次重建都会重新执行，因此本文件必须幂等 !!
--     - 建库 / 建表使用 IF NOT EXISTS
--     - 测试数据插入放在 03_seed_test_connection.sh（先查后插）
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
-- 2. 关于副本数（重要，实测结论）
--
-- SPRINT_0.md 第 11 节要求 test_connection 表使用 replication_num = 1，
-- 本文件在建表语句的 PROPERTIES 中显式声明。
--
-- !! 注意：不存在名为 default_replication_num 的系统变量 !!
--   实测 Doris 4.1.4：
--     ALTER SYSTEM SET default_replication_num = 1;
--     -> ERROR 1105: mismatched input 'default_replication_num'
--                    expecting 'LOAD'
--     SET default_replication_num = 1;
--     -> ERROR 1105: Unknown system variable 'default_replication_num'
--   若在初始化脚本中使用该语句，会直接中断整个脚本，
--   导致后续建表与插入都不执行（曾实际发生过）。
--
--   因此：**每张表都必须显式写 PROPERTIES("replication_num" = "1")**。
--   不加 PROPERTIES 时 Doris 默认 replication_num = 3，
--   单 BE 环境会报：
--     replication num should be less than the number of available
--     backends. replication num is 3, available backend num is 1
-- ------------------------------------------------------------

-- 注意：这里不用反引号包围库名。
--   实测 `USE \`ecommerce\`;` 在部分客户端下会报
--   "USE must be followed by a database name"，
--   而 `USE ecommerce;` 正常。
USE ecommerce;

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
-- 4. 测试数据
--
-- 说明（实测）：
--   - Doris 不支持 MySQL 风格的 `DELETE FROM t WHERE ...`
--     （会报错），DUPLICATE KEY 模型上亦不保证逐行删除语义；
--   - 也不支持 `INSERT ... SELECT ... WHERE NOT EXISTS` 这种写法。
--
--   因此本文件只负责建库建表，**测试数据的插入放在
--   03_seed_test_connection.sh 中**，用「先查后插」实现幂等：
--   容器每次重建都会执行初始化目录，若在此直接 INSERT，
--   重复执行会产生重复行（DUPLICATE KEY 模型不去重）。
-- ------------------------------------------------------------

-- ------------------------------------------------------------
-- 5. 校验：查询结果会出现在 BE 容器日志中
-- ------------------------------------------------------------
SELECT * FROM `test_connection`;
