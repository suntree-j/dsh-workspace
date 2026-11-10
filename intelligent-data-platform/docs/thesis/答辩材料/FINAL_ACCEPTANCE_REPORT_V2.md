# 最终验收报告 V2（真实数据版）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 验收对象：**已切换为两个公开真实数据集**的完整链路（MySQL → Kafka → Flink/Doris 实时；MySQL/Kafka → Spark → 湖仓 → Iceberg 离线；逐窗口批流对账；数据服务、看板与 Agent）
> 验收日期：2026-11-11（Asia/Shanghai）
> 数字来源：[`docs/thesis/REAL_DATA_FACTS.md`](../REAL_DATA_FACTS.md)（唯一数字来源）
> 前序报告：[`FINAL_ACCEPTANCE_REPORT.md`](FINAL_ACCEPTANCE_REPORT.md)（合成数据阶段，已标注为历史记录，**不与本报告混用数字**）

---

## 1. 结论

**验收结论：PASS（范围受限的通过）。**

支撑该结论的硬证据共七项，全部为本日实测、可复现：

| 编号 | 证据 | 实测值 |
|---|---|---|
| E1 | 交易域批流对账零不一致 | 40,358 / 40,358 窗口一致，不一致 **0**，两侧 GMV 均 **17,067,427.47** |
| E2 | 流量域批流对账零不一致 | 12,957 / 12,957 窗口一致，不一致 **0**，单边 0/0，两侧 PV 均 **10,096,157** |
| E3 | 全量数据质量校验 | `--with-lake` 下 **25/25 通过**，`dwd_ods_parity_all` 不一致表数 0 |
| E4 | 湖仓落地 | Iceberg **23 张表**，DWD 与 ODS 逐表行数相等 |
| E5 | 数据服务 | `/data/`、`/api/health`、`/api/overview`、`/agent/health` 全部 HTTP 200 |
| E6 | 派生比率列自洽 | 实时侧 12,960 窗口全部自洽，异常 **0**（此前 1,302 个窗口矛盾，根因已修） |
| E7 | 性能基线 | 数据量放大两个数量级后，聚合 8.0 ms、`/overview` 68.3 ms，与合成阶段基本持平 |

**本报告不主张的内容（防止误引）**：不主张生产可用、不主张高并发、不主张跨域关联分析、
不主张天池全量入库（实际为按用户抽样的 10.1%）、不主张支付失败链路（源数据无失败事件）。

---

## 2. 验收范围与方法

| 项 | 说明 |
|---|---|
| 验收环境 | 京东云 `lavm-txyjj7xzr7`，Ubuntu 24.04.2 LTS，4 核 / 16 GB，Docker Compose 14 容器 |
| 验收方法 | 逐项跑仓库内脚本 + 直接查 Doris/Iceberg/MySQL + 打 HTTP 接口，**不接受文档转述** |
| 判定原则 | ①可加/去重计数类指标必须**逐窗口严格相等**；②派生比率列不参与 `is_pass`，但必须**由自身计数自洽**；③任何不一致都必须定位到窗口并归因，不允许"整体通过"掩盖单点 |
| 数据版本 | 交易域 UCI Online Retail II 全量两年；流量域天池 Taobao UserBehavior 抽 10 万用户 × 9 天 |
| 采集时间 | 2026-11-11 00:42 ~ 01:15 |

---

## 3. 数据来源与许可

| | 交易域 | 流量域 |
|---|---|---|
| 数据集 | UCI Machine Learning Repository, Online Retail II | 天池，淘宝用户行为数据集 |
| 许可 | CC BY 4.0 | CC BY-SA-NC 4.0 |
| DOI / 地址 | `10.24432/C5CG6D` | `tianchi.aliyun.com/dataset/649` |
| 原始规模 | 1,067,371 行 | 100,150,807 行 |
| 使用方式 | **全量**清洗入库 | 按用户抽样 10 万人（10,098,974 条，占 10.1%） |
| 校验 | 行数与官方口径一致；压缩包 MD5 与随附 `.md5` 一致 | 行数 100,150,807，与官方口径一致 |

两个数据集在用户、商品、时间、国家上**互不重叠**，因此按两个并列业务域处理，主键用偏移量隔离
（交易域 `< 10,000,000`，流量域 `+10,000,000`）。
许可与出处已作为两条 `[DS/OL]` 类型文献写入开题报告与论文参考文献。

---

## 4. 数据入库完整性

| 表 | 行数 | 校验 |
|---|---|---|
| `orders` | 776,575 | `quantity × price == amount` **776,575 / 776,575 精确相等** |
| `payment` | 776,575 | 与 `orders` 一一对应 |
| `refund` | 16,552 | 外键悬空 0 |
| 交易域 `user` / `product` | 5,852 / 4,619 | 外键悬空 0 |
| 流量域行为明细 | 10,098,974 | `event_id` 唯一，三级漏斗单调 |
| 流量域 `user` / `product` | 100,000 / 1,598,617 | 商品按主导类目去重后无重复主键 |

MySQL 五表外键悬空全 0；两域 id 段隔离实测：`user_id < 1000 万` 1,660 行 + 流量域 4,990 行，
`product_id < 1000 万` 2,690 行 + 流量域 1,598,617 行（两者在 ODS 合并后为 `dim_user 105,852`、`dim_product 1,603,236`）。

> **一个曾经被我误读的比对口径**：`SUM(ABS(orders.quantity × product.price − orders.amount) < 0.005)`
> 只有约 9 成命中。原因是 UCI 同一商品在不同发票里单价不同，`product.price` 只是代表价，
> `orders.amount` 按下单当时的真实单价计算。**这条比对本身是错的**，正确口径是
> `quantity × 下单单价 == amount`，实测 776,575/776,575 精确相等。

---

## 5. 实时链路

| 项 | 实测 |
|---|---|
| Flink 作业 | **8 个 RUNNING**（4 个 DWD sink + 4 个指标 sink） |
| Doris Routine Load | **8 个 RUNNING**，总 Lag = 0 |
| 实时 DWD | 订单 776,575 / 支付 776,575 / 退款 16,552 / 行为 10,098,974 |
| 实时 ADS | `ads_realtime_trade_1m` 40,360；`ads_realtime_traffic_1m` 12,960；`dws_traffic_overview_1m` 12,960；`ads_realtime_category_1m` 178,681 |
| 实时 ADS vs 实时 DWD 重聚合 | 交易域 dwd_win 34,272 / ads_win 40,360 / GMV 两侧 **17,068,016.12** / 不一致 **0**；流量域 12,960 / 12,960 / PV 两侧 **10,098,974** / 不一致 **0** |
| 时间语义 | 按**事件时间**开窗（`WATERMARK FOR event_time AS event_time - INTERVAL '5' SECOND`），因此 2009–2011 / 2017 的历史事件可以完整跑通 |

**一次真实故障与修复（写进论文第 6 章）**：首次全量重载在 4,964,900 条行为处
`java.lang.OutOfMemoryError: Java heap space`，两个 sink 变 FAILED。
根因是 `.env` 里 `FLINK_TASKMANAGER_MEMORY=2048m` 覆盖了 compose 默认值，
8 个 slot 下每 slot 仅约 200 MB 堆。修法：TaskManager 提到 6144m，
`managed.fraction` 与 `network.fraction` 降到 0.1，metaspace 256m。修复后全额产出、零丢失。

---

## 6. 批处理链路

`bash scripts/batch-mode.sh` 一次跑通，各阶段自带校验全部通过：

| 阶段 | 校验 |
|---|---|
| ods | 5/5（5 张 ODS 表与 MySQL **精确一致**） |
| archive | 16/16 |
| dwd | 10/10 |
| dws | 13/13 |
| ads | 34/34 |
| traffic-dwd | 21/21 |
| traffic-dws | 20/20 |
| traffic-ads | 36/36 |
| reconcile | 5/5 |
| traffic-reconcile | 10/10 |
| iceberg-migrate | 70/70 |

批层 ADS：`ads_batch_trade_1m` 40,360 / `ads_batch_trade_1d` 604 / `ads_batch_category_1m` 178,681 /
`ads_batch_category_1d` 6,631 / `ads_traffic_1m` 12,960 / `ads_traffic_1d` 9。
**Doris 与湖仓 12 张表行数逐表相等。**

---

## 7. 批流对账（交易域）

| 项 | 值 |
|---|---|
| 批次 | `reconcile_20261111_001950` |
| 对账区间 | 2009-12-01 07:45:00 ~ 2011-12-09 12:48:00 |
| 实时 / 批层 / 一致窗口 | 40,358 / 40,358 / **40,358** |
| 不一致窗口 | **0**，`first_mismatch_at = NULL` |
| 两侧 GMV 合计 | **17,067,427.47 = 17,067,427.47** |
| 明细 `is_match` | 40,358 / 40,358 全真 |
| `is_pass` | 1 |

判据口径：交易域 **7 个可加列 + 1 个去重列**（`gmv` / `order_cnt` / `payment_cnt` / `payment_amount` /
`payment_fail_cnt` / `refund_cnt` / `refund_amount` 为可加；`order_user_cnt` 为逐窗口去重、只做逐窗口比对）。
派生比率（客单价、支付成功率、退款率）**不进 `is_pass`**，只在诊断表里登记。

---

## 8. 批流对账（流量域）

| 项 | 值 |
|---|---|
| 批次 | `reconcile_traffic_20261111_002245` |
| 对账区间 | 2017-11-25 00:00:00 ~ 2017-12-03 23:57:00 |
| 实时 / 批层 / 一致窗口 | 12,957 / 12,957 / **12,957** |
| 不一致窗口 | **0** |
| 单边窗口 | 仅实时 **0** / 仅离线 **0** |
| 两侧 PV 合计 | **10,096,157 = 10,096,157** |
| 窗口 UV 范围 | 实时 [1, 97] = 批层 [1, 97] |
| 比率列异常窗口 | 实时 **0** / 批层 **0** |
| `is_pass` | 1 |

判据口径：**7 个判据列**（`uv` / `pv` / `view_cnt` / `click_cnt` / `cart_cnt` / `favorite_cnt` / `buy_cnt`）。
`click_cnt` 恒为 0（天池无点击事件），`click_rate = 0.0000` 是"点击数为 0"的真实结果，
`cart_rate = NULL` 是"分母为 0"的口径约定，两者都合规。

---

## 9. 零值窗口与判别力（必须与"不一致 0"一起讲）

| | 交易域 | 流量域 |
|---|---|---|
| 窗口总数 | 40,360 | 12,960 |
| 两侧同为 0 的窗口 | **6,088（15.08%）** | **0** |
| 有非零业务量的窗口 | **34,272（84.92%）** | 12,960 |

交易域有 15.08% 的分钟窗口两侧都是 0，这类窗口的"一致"是**平凡一致**，判别力来自其余 34,272 个窗口。
流量域 9 天共 12,960 个分钟窗口**全部有事件**（0 个零值窗口），因为 10 万用户 × 9 天的行为密度足以覆盖每一分钟。

> 与旧合成数据对比：合成阶段的零值窗口是 5,504 个（48.03%），
> 真实数据的 15.08% 说明判别力**更强**，这是一个可以直接讲的优势。

---

## 10. 数据质量校验

| 命令 | 结果 |
|---|---|
| `bash scripts/run-quality-checks.sh` | 通过 24 / 失败 0 / 跳过 1 |
| `bash scripts/run-quality-checks.sh --with-lake` | **通过 25 / 失败 0 / 跳过 0** |

25 条校验覆盖：主键唯一、非空、外键悬空、行数守恒、层间一致性（DWD == ODS 逐表）、
漏斗单调（`VIEW > CART > BUY`，CLICK 存在时须严格居中）、金额守恒、
批层与流层的推进滞后（≤ 48 小时）、对账汇总 `is_pass`。

**新增证据**：`funnel_monotonic` 判据已从写死四级改为"三级必检、CLICK 存在时自动切换到四级"，
因为天池没有 CLICK 事件；写死四级会让"漏斗少一级"与"漏斗坏了"无法区分。

---

## 11. 湖仓与 Iceberg

| 项 | 实测 |
|---|---|
| Iceberg 表数 | **23**（`SHOW TABLES IN iceberg.lakehouse_iceberg`） |
| 表格式 | v2 / snappy，HiveCatalog，`warehouse = s3a://lakehouse/warehouse` |
| 分层 | ODS 6 / DWD 6 / DWS 5 / ADS 6（含 2 张对账在 Doris 侧汇总） |
| DWD 与 ODS | 逐表行数**完全相等**（105,852 / 1,603,236 / 776,575 / 776,575 / 16,552 / 10,098,974） |
| 与 Doris 的一致性 | 12 张批层表行数逐表相等 |

DWS 五张表**只在 Iceberg，不在 Doris**（Doris 侧查会报 `Table [dws_xxx] does not exist in database`），
这是设计选择：DWS 面向离线分析，不参与实时查询。

---

## 12. 服务层（数据服务 / Agent）

| 项 | 实测 |
|---|---|
| 数据服务 | `/data/`、`/data/api/health`、`/data/api/overview`、`/data/agent/health` 全部 **200**，经 nginx 反代 |
| `/data/api/overview` | `gmv 17068016.12` / `order_cnt 776575` / `refund_amount 688138.45` / `pv 10098974` / `uv 100000` / `buy_rate 0.3636` |
| Agent | `docs_total 66`（metric 22 / convention 16 / topic 4 / layering 8 / table 16），后端 `lexical-bm25`，**零向量依赖** |
| Agent 取数边界 | 进程内**无数据库凭据**，只经 HTTP `POST /data/api/query`，过 `sqlguard` + 只读账号 `agent_ro` |
| Agent 端到端 | `/data/agent/ask` 能答出两套口径并指出差异；重试机制实测有效（`DORIS_QUERY_ERROR_1105` → 反思 → 换表 → 成功，`retries=1`） |

---

## 13. 前端

| 项 | 实测 |
|---|---|
| 页面 | 10 个路由，5 个导航分组，零构建 SPA（Vue 3 全局 + ECharts） |
| 漏斗口径 | 已改为**服务端全量聚合**（`/funnel?window_limit=20160`），无 CLICK 时自动降为三级并改文案 |
| 一致性 | 页面 PV/UV/BUY 与 `/overview` 同源，不再出现"1,245,278 vs 9,045,077"的自相矛盾 |
| 静态资源 | `app.js` 本地与服务端 md5 一致，`node --check` 通过 |

---

## 14. 性能基线

采集脚本 `scripts/perf/measure-latency.sh`，n=7 取中位数，本机回环，无并发、热缓存。
现场：load 1.35 / 1.73 / 4.71，可用内存 4,666 MB，Spark 作业 0 个。

| 路径 | 中位数 | 合成阶段对照 |
|---|---|---|
| 点查（10 行） | 12.0 ms | 8.4 ms |
| 聚合（全量 SUM/COUNT） | 8.0 ms | 8.2 ms |
| 两表关联 + GROUP BY | 8.4 ms | 10.1 ms |
| `GET /overview` | 68.3 ms | 69.1 ms |
| `GET /meta/metrics` | 1.8 ms | — |
| `GET /batch/reconcile` | 28.2 ms | — |
| 实时 1 分钟窗口聚合 | 8.6 ms | — |
| 离线 按天聚合 | 7.5 ms | — |

**结论**：数据量放大两个数量级，聚合、关联、`/overview` 延迟**基本不变**（查询带窗口过滤 + `ORDER BY ... DESC LIMIT`，走前缀索引）。
只有点查从 8.4 → 12.0 ms，因为 `orders` 从 6,000 行涨到 776,575 行。
**这些数字不构成高并发证明**：无并发、热缓存、本机回环。

---

## 15. 可复现性

| 环节 | 命令 |
|---|---|
| 交易域建集 | `python scripts/real-data/build_uci_dataset.py`（窗口 2009-12-01 ~ 2011-12-10） |
| 流量域抽样 | `N_USERS=100000` / `WIN_LO=1511539200` / `WIN_HI=1512316800` 跑 `tianchi_sample.py` |
| 流量域建集 | `python scripts/real-data/build_tianchi_dataset.py`（商品按主导类目去重） |
| 装载 | `python -m src.load_real_data --dir data/real --step all` |
| 全链路 | `bash scripts/batch-mode.sh` |

可复现性来源从"固定随机种子"改为**三项固定**：数据集版本 + 抽样规则 + 版本固定的映射脚本。
换人重跑得到逐字节相同的 CSV（流量域抽样按 `timestamp → user_id → item_id → behavior` 稳定排序）。

---

## 16. 安全与边界

| 项 | 实测 |
|---|---|
| SQL 守卫 | `sqlguard` 白名单 + 只读账号；逗号连接绕过漏洞已修（`FROM dim_user a, ecommerce.ods_user b` 曾被放行，现 `REJECT TABLE_NOT_ALLOWED`） |
| Agent 权限 | 无数据库凭据，仅 HTTP + `sqlguard`；跨表/写操作被拒 |
| 公开暴露面 | 仅 80 端口（nginx 反代 `/data/`）；443 未启用（明文 HTTP，按用户决定先走 IP） |
| 服务器清理 | 除本项目外的另一个项目 `ai-comic-studio` 已停用归档（120 MB 留在 `/opt/.archive/`），`/opt` 只剩 `containerd` 与 `data-platform` |

---

## 17. 已知限制（必须与结论同时陈述）

1. 两个数据集**互不重叠**，不是同一业务的交易与行为，做不了跨域漏斗与归因。
2. 流量域是**按用户抽样的 10.1%**（10,098,974 / 100,150,807），不是天池全量。
3. UCI **无类目字段**，11 个类目由商品描述关键词派生。
4. UCI **约 22.8% 的行无 Customer ID**，不进入 `orders`。
5. UCI **退款无法按发票号关联**原始订单（匹配率 0/8,292），按 `(user_id, product_id)` 关联最早订单。
6. 天池**无金额、无订单号、无 CLICK**，支付失败笔数恒为 0。
7. 两域类目命名体系不同（中英文/粒度不同），`dim_product` 里并存未做映射。
8. 时间戳保留原始事件时间，页面"最新窗口"显示数据时间而非当前时间。
9. 恢复实时链路会触发**整代重放**（Flink `earliest-offset`），指标 topic 长度翻倍，属设计行为。
10. 单机部署、无并发、无高可用，性能数字不代表生产规模。

---

## 18. 复核指引

```bash
# 1. 数据规模
bash scripts/run-quality-checks.sh --with-lake      # 期望：通过 25 / 失败 0 / 跳过 0
# 2. 对账结论（Doris）
#    SELECT * FROM lakehouse_ads.ads_reconcile_summary;
#    SELECT * FROM lakehouse_ads.ads_reconcile_traffic_summary;
#    期望：mismatched_windows = 0，is_pass = 1，两侧合计相等
# 3. 实时层
#    SELECT COUNT(*) FROM ecommerce.ads_realtime_trade_1m;      -- 40360
#    SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail; -- 10098974
# 4. 湖仓
bash scripts/spark-sql.sh -e "SHOW TABLES IN iceberg.lakehouse_iceberg;"   # 期望 23 行
# 5. 接口
curl -s http://127.0.0.1:8000/data/api/overview | head -c 400
# 6. 性能
bash scripts/perf/measure-latency.sh
```

**判定**：E1–E7 七项硬证据全部满足，且第 17 节的十条限制已如实写入论文、答辩材料与 README，
因此给出 **PASS**。若引用本报告的结论，必须同时陈述第 17 节的限制——
"零不一致"成立的范围是**对账区间之内**，且建立在**已声明的两个真实数据集子集**上。

---

*本报告所有数字均可在 `docs/thesis/REAL_DATA_FACTS.md` 找到对应命令行；两者不一致时以事实表为准。*
