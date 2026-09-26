# Sprint 0 验收状态（Verification Status）

> 更新日期：2026-09-26
> 对应任务书：[`SPRINT_0.md`](SPRINT_0.md) 第 22、29 节
> 验收环境：**腾讯云服务器 36.151.150.140（Ubuntu 24.04.2 LTS / Docker 29.8.1 / Compose v5.5.1）**

---

## 0. 当前结论

```text
✅ Sprint 0 已在真实容器环境中完成全部验收
   - health-check.sh：5/5 服务全部 [OK]
   - pytest：51 passed（24 单元 + 27 冒烟）
   - 数据生成：MySQL 5 表 + Kafka 4 Topic 全部落库
   - 持久化：docker compose down / up 后数据完全保留
```

一键复现：

```bash
cd /opt/data-platform
bash scripts/verify-sprint-0.sh     # config + up + ps + health-check + pytest
```

---

## 1. 基础设施（全部实测通过）

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| Docker Compose 可启动 | ✅ | `docker compose up -d` 退出码 0 |
| MySQL 正常 | ✅ | `mysqladmin ping` + healthy，RestartCount=0 |
| Kafka 正常 | ✅ | `kafka-broker-api-versions.sh` 通过，healthy |
| MinIO 正常 | ✅ | healthcheck（基于 mc）healthy |
| Doris FE 正常 | ✅ | healthy，RestartCount=0 |
| Doris BE 正常 | ✅ | healthy，BE 已注册且 `Alive=true` |
| 统一网络 `data-platform` | ✅ | 子网 172.28.0.0/24 |
| 命名卷持久化 | ✅ | 8 个 `data-platform-*` 卷 |

`scripts/health-check.sh` 实际输出：

```text
=====================================
 Data Platform Health Check
=====================================

[OK] MySQL
[OK] Kafka
[OK] MinIO
[OK] Doris FE
[OK] Doris BE

=====================================
 All services are healthy
=====================================
```

## 2. 数据（全部实测通过）

| 验收项 | 结果 | 证据 |
| --- | --- | --- |
| MySQL `ecommerce` 库 | ✅ | `information_schema.schemata` 命中 |
| MySQL 5 张业务表 | ✅ | user / product / orders / payment / refund |
| 表字段完整 | ✅ | 冒烟测试逐表校验字段集合 |
| Kafka 4 个 Topic | ✅ | order/payment/refund/behavior_event，各 3 分区、副本 1 |
| MinIO `lakehouse` bucket | ✅ | `mc ls local` 显示 |
| Doris `ecommerce` 库 | ✅ | `SHOW DATABASES` 命中 |
| Doris `test_connection` 表 | ✅ | `SELECT *` 返回 `doris connection ok` |

## 3. 数据生成器（全部实测通过）

实测规模：

```text
user     1200 行
product   600 行
orders   6000 行
payment  5406 行
refund    254 行
```

Kafka 事件（31,660 条，持久化后数量一致）：

| Topic | 条数 |
| --- | --- |
| order_event | 6000 |
| payment_event | 5406 |
| refund_event | 254 |
| behavior_event | 20000 |

行为漏斗（逐级收窄 ✅）：

```text
VIEW 10472 > CLICK 5759 > CART 2095 > BUY 628
FAVORITE 1046（旁支，少于 VIEW）
```

业务关系校验（生成器内置，连表查询）：

```text
[OK] order.user_id 全部存在于 user
[OK] order.product_id 全部存在于 product
[OK] payment.order_id 全部存在于 orders
[OK] refund.order_id 全部存在于 orders
[OK] 金额一致性抽样 200 单：price × quantity ≈ amount
```

发送性能：31,660 条 / 0.64 秒 ≈ 49,572 条/秒。

## 4. 测试（全部实测通过）

```text
51 passed in 26.62s
├── 24 passed  单元测试（tests/test_data_generator.py，无需容器）
└── 27 passed  冒烟测试（tests/smoke/test_infrastructure.py，真实执行）
```

冒烟测试覆盖任务书第 19 节全部 10 项，且**真实执行**而非检查文件存在：

```text
1.  MySQL 可以连接（mysqladmin ping + 版本 8.4.11）
2.  ecommerce 数据库存在
3.  5 张业务表存在且字段齐全
4.  Kafka Topic 存在（并校验分区数=3、副本=1）
5.  Kafka 可以生产消息
6.  Kafka 可以消费消息（端到端 Producer -> Kafka -> Consumer）
7.  MinIO Bucket 存在（通过 mc 校验）
8.  Doris 可以连接
9.  Doris 可以建表（建表后校验并清理）
10. Doris 可以查询测试数据（test_connection）
```

## 5. 持久化（实测通过）

执行 `docker compose down` 后 `docker compose up -d`：

| 数据 | down 前 | down/up 后 |
| --- | --- | --- |
| MySQL user/product/orders/payment/refund | 1200/600/6000/5406/254 | **完全一致** |
| Doris test_connection | 1 行 | **1 行** |
| Kafka 4 Topic 消息总数 | 31660 | **31660** |
| MinIO lakehouse bucket | 存在 | **存在** |

## 6. 脚本（实测通过）

| 脚本 | 结果 |
| --- | --- |
| `scripts/start.sh` | ✅ 可用 |
| `scripts/stop.sh` | ✅ 可用（默认保留数据卷） |
| `scripts/status.sh` | ✅ 可用 |
| `scripts/health-check.sh` | ✅ 5/5 [OK]，退出码 0；失败时退出码 1 |
| `scripts/verify-sprint-0.sh` | ✅ 一键全链路验收 |

## 7. 安全（实测通过）

| 验收项 | 结果 |
| --- | --- |
| 未提交 `.env` | ✅ `git check-ignore` 命中 |
| 无真实口令 / API Key / Token | ✅ `.env.example` 全为 `change_me_*` |
| 服务器 `.env` 权限 | ✅ `600`，口令为 192-bit 随机值 |
| 运行时产物未入库 | ✅ 快照/venv/缓存均忽略 |
| Shell 脚本 LF 行尾 | ✅ 逐字节校验 CRLF=0 |

---

## 8. 本次验收中发现并修复的问题

均为**只有在真实容器中运行才会暴露**的问题，已全部修复并提交：

| # | 问题 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | `minio-init` 卡在重试直到超时，bucket 未创建 | minio 镜像基于 BusyBox，**不含 sed/awk/grep/curl/tar**；脚本用 `sed` 剥离协议前缀导致 `MC_HOST_local` 为空 | 改用 POSIX 参数展开 `${var#http://}`；就绪探测改用 `mc ls` |
| 2 | minio healthcheck 永远失败/误报 | 用镜像内不存在的 `curl`；且 `mc ready <url>` 失败时**退出码为 0** | 新增 `infrastructure/minio/healthcheck.sh`，用 mc 显式构造别名后 `mc ls` |
| 3 | Doris 初始化 SQL 未生效，`test_connection` 未创建 | 第 42 行 `ALTER SYSTEM SET default_replication_num = 1` 在 Doris 4.1.4 **不存在该系统变量**，直接中断整个脚本 | 删除该语句；改为每张表显式声明 `PROPERTIES("replication_num"="1")`，并在 SQL 中记录原因 |
| 4 | Doris BE **每 ~15 秒重启一次**（RestartCount 持续增长） | 上游 `apache/doris:be-4.1.4` 的 `entry_point.sh` 在 `check_be_status` 检测到 BE 就绪后即返回，容器 PID 1 退出，Docker 随即终止 doris_be 并重启 | 新增 `infrastructure/doris/be-keepalive.sh` 保活包装（`entrypoint` 覆盖），已验证 150 秒 0 重启 |
| 5 | 数据生成器写快照 `PermissionError` | 容器以非 root 的 appuser 运行，而 `./data-generator` 以宿主机属主挂载，`/app/state` 不可写 | 快照目录改用命名卷 `data-platform-data-generator-state` |
| 6 | 冒烟测试 3 处自身缺陷 | 缺 fixture 参数、Kafka describe 概要行被计入分区数、用镜像内不存在的 curl 探 MinIO | 逐一修正（详见提交 `fix: correct minio healthcheck and init for busybox image`） |
| 7 | `test_doris_default_replication_num_is_one` 断言了不存在的变量 | 与 #3 同一根因 | 改为验证真实约束：不带 PROPERTIES 建表应失败、带则成功 |

### 8.1 环境适配（非代码问题）

| 问题 | 处理 |
| --- | --- |
| `get.docker.com` 被重置、`registry-1.docker.io` 不可达 | 改用清华 `docker-ce` apt 源 + 国内 registry mirror（见 `.deploy/README.md`） |
| `vm.max_map_count` 仅 1048576，低于 Doris 要求 | 写入 `/etc/sysctl.d/99-data-platform.conf` 设为 2000000 |
| Doris 镜像经镜像站拉取仅 ~1.5 MB/s | 用 SSH 反向隧道共享本地代理，FE 12 秒完成拉取 |
| 本地 `~/.ssh/config` 带 UTF-8 BOM，Git Bash 报 `Bad configuration option` | 备份后移除 BOM |

### 8.2 尚未处理的事项

| 事项 | 说明 |
| --- | --- |
| Doris BE 中的遗留 backend | 排查期间用临时容器注册过 `172.28.0.12`，BE 列表中仍可见。不影响功能；如需清理：`ALTER SYSTEM DROP BACKEND "172.28.0.12:9050"` |
| 服务器无 SWAP | Sprint 0 的 16 GB 足够；后续同时运行 Flink/Spark 时建议增加 |
| Doris BE 保活包装 | 属上游问题规避手段，上游修复后应移除 `entrypoint` 覆盖 |

---

## 9. 复现方式

```bash
# 服务器上（/opt/data-platform）
docker compose config --quiet
bash scripts/health-check.sh
.venv/bin/python -m pytest -q
docker compose run --rm -T data-generator python -m src.generate_mysql_data --reset
docker compose run --rm -T data-generator python -m src.generate_events
```

---

## 10. 更新要求

后续每次变更后，**必须重新执行验收并更新本文档**。

> 依据 `AGENTS.md` 第 8 节：**禁止把未验证项写成已验证项。**
