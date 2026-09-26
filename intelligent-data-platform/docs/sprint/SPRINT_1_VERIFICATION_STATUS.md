# Sprint 1 验收状态（Verification Status）

> 更新日期：2026-09-26
> 对应设计：[`SPRINT_1.md`](SPRINT_1.md)（含第 11 节实现偏差记录）
> 验收环境：**腾讯云服务器 36.151.150.140（Ubuntu 24.04.2 LTS / Docker 29.8.1 / Compose v5.5.1）**
> 指标口径：[`sql/metadata/metrics.md`](../../sql/metadata/metrics.md)

---

## 0. 当前结论

```text
✅ Sprint 1 已在真实容器环境中完成全部验收

   链路：Kafka 事件 → Flink 清洗/窗口聚合 → Kafka 指标 → Doris Routine Load → DWD/DWS/ADS

   ✅ 8 个 Flink sink 作业各 1 个实例（无重复、无遗留）
   ✅ 8 个 Routine Load 作业 RUNNING，errorRows 全部为 0
   ✅ health-check.sh 11/11 [OK]，退出码 0（Sprint 0 五项 + Sprint 1 六项）
   ✅ pytest -m smoke：74 passed（27 基础设施 + 47 实时链路）
   ✅ 指标与 MySQL 精确对账：GMV 51890375.77 == 51890375.77（精确到分）
```

一键复现：

```bash
cd /opt/data-platform

bash scripts/verify-sprint-1.sh              # 验收（不动数据）：链路就绪 + 健康检查 + 测试 + 对账
bash scripts/verify-sprint-1.sh --replay     # 重建链路并重放数据后再验收（会清空下游派生数据）
```

---

## 1. 验收步骤与结果

| # | 步骤 | 结果 | 证据 |
| --- | --- | --- | --- |
| 0 | 重建链路并重放数据（`--replay` 时执行） | ✅ | 12 个 topic 重建、8 张表清空、31,660 条事件重新生成 |
| 1 | 环境检查（docker / compose / python） | ✅ | Docker 29.8.1、Compose 5.5.1、Python `.venv` |
| 2 | `docker compose config --quiet` | ✅ | 编排文件语法与变量插值全部通过 |
| 3 | `docker compose up -d` | ✅ | 9 个常驻容器全部 Running，核心服务 healthy |
| 4 | 等待实时链路就绪 | ✅ | Flink 作业 8/8 且**各 1 个实例**，Routine Load 8/8 RUNNING |
| 5 | `scripts/health-check.sh` | ✅ | 11/11 [OK]，`All services are healthy`，退出码 0 |
| 6 | `python -m pytest -m smoke` | ✅ | `74 passed, 24 deselected in 50.35s` |
| 7 | 数据对账（MySQL ↔ DWD ↔ ADS） | ✅ | 8 项对账全部 [PASS]（见第 3 节） |

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

 Sprint 1 — 实时数仓链路
[OK] Flink 集群
[OK] Flink 作业唯一性
[OK] Flink SQL Gateway
[OK] Kafka 实时 Topic
[OK] Doris 实时表
[OK] Doris Routine Load

=====================================
 All services are healthy
=====================================
```

---

## 2. 数据链路（逐层实测）

| 层 | 对象 | 实测值 | 与上游一致 |
| --- | --- | --- | --- |
| 源 | Kafka `order_event` | 6,000 条 | = MySQL `orders` 行数 |
| 源 | Kafka `payment_event` | 5,406 条 | = MySQL `payment` 行数 |
| 源 | Kafka `refund_event` | 254 条 | = MySQL `refund` 行数 |
| 源 | Kafka `behavior_event` | 20,000 条 | 生成器上报条数 |
| DWD | `dwd_trade_order_detail` | 6,000 行 | ✅ 与源 topic 一致 |
| DWD | `dwd_trade_payment_detail` | 5,406 行 | ✅ |
| DWD | `dwd_trade_refund_detail` | 254 行 | ✅ |
| DWD | `dwd_traffic_behavior_detail` | 20,000 行 | ✅ |
| DWS | `dws_traffic_overview_1m` | 19,644 行（窗口） | PV 合计 20,000 ✅ |
| ADS | `ads_realtime_trade_1m` | 11,459 行（窗口） | 见第 3 节 |
| ADS | `ads_realtime_traffic_1m` | 19,644 行（窗口） | PV 合计 20,000 ✅ |
| ADS | `ads_realtime_category_1m` | 5,998 行（窗口 × 类目） | 订单 6,000 / GMV 一致 ✅ |

Routine Load 全部 RUNNING 且无错误行：

```text
  rl_dwd_trade_order_detail        loadedRows=6000    errorRows=0
  rl_dwd_trade_payment_detail      loadedRows=5406    errorRows=0
  rl_dwd_trade_refund_detail       loadedRows=254     errorRows=0
  rl_dwd_traffic_behavior_detail   loadedRows=20000   errorRows=0
  rl_dws_traffic_overview_1m       loadedRows≈19644   errorRows=0
  rl_ads_realtime_trade_1m         loadedRows≈29450   errorRows=0
  rl_ads_realtime_traffic_1m       loadedRows≈20000   errorRows=0
  rl_ads_realtime_category_1m      loadedRows≈6000    errorRows=0
```

> 说明：ADS/DWS 的 `loadedRows` 大于窗口数属于正常现象 ——
> upsert-kafka 对同一窗口会写多条更新消息（窗口未最终触发前的中间态），
> Doris 侧按 `UNIQUE KEY(window_start)` 收敛为最新值。
> 另有少量**墓碑消息**（value=null，实测 29,475 条消息中 25 条），
> Doris 会直接跳过，不计入错误行。

---

## 3. 指标对账（精确相等）

| 对账项 | ADS 实际值 | MySQL 实际值 | 结果 |
| --- | --- | --- | --- |
| 订单量 `SUM(order_cnt)` | 6,000 | 6,000 | ✅ |
| GMV `SUM(gmv)` | 51,890,375.77 | 51,890,375.77 | ✅ **精确到分** |
| 支付笔数 `SUM(payment_cnt)` | 5,287 | 5,287 | ✅ |
| 退款笔数 `SUM(refund_cnt)` | 254 | 254 | ✅ |
| 类目订单量 `SUM(order_cnt)` | 6,000 | 6,000 | ✅ |
| 类目 GMV `SUM(gmv)` | 51,890,375.77 | 51,890,375.77 | ✅ |
| 流量 PV `SUM(pv)` | 20,000 | 20,000（行为事件数） | ✅ |
| 漏斗关系 | pv 20,000 ≥ view 10,472 ≥ buy 628 | — | ✅ |

对账口径：1 分钟窗口按事件时间切分且不重叠，因此**逐窗口可加指标求和必然回到全量**。
能做到精确相等就不设置"允许偏差" —— 一旦不相等即代表真的丢数或重复计算。

---

## 4. 冒烟测试覆盖

```text
tests/smoke/test_infrastructure.py   27 个用例（Sprint 0：MySQL / Kafka / MinIO / Doris）
tests/smoke/test_realtime.py         47 个用例（Sprint 1：Flink / Topic / 表 / 指标对账）
tests/smoke/conftest.py              共用前置条件（docker 定位、.env 读取、容器就绪判断）
```

`test_realtime.py` 覆盖范围：

| 分组 | 内容 |
| --- | --- |
| Flink 集群 | JobManager REST、TaskManager slots、8 个作业全部 RUNNING |
| SQL Gateway | `/v1/info` 可用 |
| 下游 Topic | 8 个 topic 存在（参数化） |
| Doris 表 | 8 张表存在且字段数 ≥ 5（防"空壳表"，参数化） |
| Routine Load | 8 个作业 RUNNING、errorRows=0、loadedRows>0（参数化 + 收敛等待） |
| DWD 对账 | 与 MySQL 事实表行数一致、无重复主键、金额精确一致、行为数与 topic 一致 |
| ADS 对账 | 交易指标与 MySQL 精确一致、可加指标无 NULL、流量与 topic 一致、ADS 与 DWS 一致、类目与 MySQL 一致 |

---

## 5. 未通过项与处理

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 首次验收 `test_routine_load_loaded_rows_positive` 失败 | ✅ 已修复 | 根因：Doris `SHOW ROUTINE LOAD` **没有顶层 `LoadedRows`/`ErrorRows` 字段**，行数藏在 `Statistic` 的 JSON 字符串里。原实现直接取 `info["LoadedRows"]`，导致该用例白等 300 秒后失败、且 `test_routine_load_no_error_rows` **假通过**。已改为解析 `Statistic` JSON，并给该用例加"限时等待收敛" |
| 首次验收 `[FAIL] Doris Routine Load` 误报 | ✅ 已修复 | 根因：解析时加了 mysql 的 `-N`，字段标签被去掉。已去掉 `-N`，并补 `USE ecommerce;` |
| 指标一度翻倍（PV 合计 39,987 vs 事件 20,000） | ✅ 已修复 | 根因：SQL Gateway 为 session 模式，重启 `flink-jobs` 容器不会取消旧作业，旧作业把重放事件累加到旧窗口状态。已新增 `scripts/cancel-flink-jobs.sh`，并在提交脚本启动时自动清场；健康检查增加「Flink 作业唯一性」检查项 |
| 类目作业提交失败 | ✅ 已修复 | 根因：Flink 写入按位置对齐，SELECT 列顺序（window_start, window_end, category_name…）与 sink/Doris 表（window_start, category_name, window_end…，因 UNIQUE KEY 必须是有序前缀）不一致 |

---

## 6. 已知限制（不影响本次验收）

| 限制 | 影响 | 记录位置 |
| --- | --- | --- |
| 源 topic 使用 `earliest-offset` | 重复生产同一批事件会让窗口指标累加；`--replay` 通过重建源 topic 构造"恰好一代事件"规避 | `SPRINT_1.md` 第 11.1 节 |
| 窗口/去重状态无 TTL | 长跑后状态增长 | 同上 |
| 未做 checkpoint 恢复演练 | 作业失败后窗口会重算 | 同上 |
| 实时与离线尚未交叉对账 | 实时指标目前只与 MySQL 对过账 | Sprint 3 |
| 服务器不可加 swap | 内存只能靠限制上限控制（实测可用 3.2 GB） | `docs/development-environment.md` 第 0.2 节 |
