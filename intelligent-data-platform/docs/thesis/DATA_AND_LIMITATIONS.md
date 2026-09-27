# 数据来源与已知限制（DATA_AND_LIMITATIONS）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 用途：**答辩防守材料**。回答"数据是真实的吗""有什么不足"这两类问题时，以本文件为准。
> 纪律：本文件汇总的每一条限制**都不是缺陷清单的补写**，而是仓库中已经记录在案的事实；来源逐条标注。
> 生成日期：2026-09-28（依据仓库现有文档整理）

---

## 第一部分 · 数据来源

### 1. 一句话结论

**本项目全部数据为程序生成的合成数据（synthetic data），不含任何真实业务数据。**

生成器同时扮演两个角色：业务系统的写入方（写 MySQL 业务库）与埋点方（写 Kafka 事件流）。系统中不存在真实电商后台，也没有接入任何外部数据源。

- **依据**：`docs/data-source-design.md` 第 1 节、第 7 节（该文件明确写"Python 数据生成器是当前阶段的「业务系统替身」"、"论文中说明为仿真数据源"）。

### 2. 生成器位置与运行方式

| 项 | 内容 |
| --- | --- |
| 位置 | `data-generator/`（Python 包 `src/`） |
| 两个正式入口 | `python -m src.generate_mysql_data`（写 MySQL 业务库）、`python -m src.generate_events`（写 Kafka） |
| 一次性跑完整链路 | `python -m src`（等价于先跑上面两个） |
| 容器内运行（推荐） | `docker compose run --rm -T data-generator python -m src.generate_mysql_data --reset` |
| 运行镜像 | `python:3.13.14-slim-bookworm`（**注意：这是全项目唯一使用 Python 3.13 的地方**） |
| 服务编排位置 | `docker-compose.yml` 的 `tools` profile —— **不会**随 `docker compose up -d` 自动启动（有意设计，避免无人值守地写入大量数据） |

**依据**：`data-generator/README.md` 第 1、2 节；`data-generator/src/config.py` 第 64~104 行。

### 3. 可复现性

| 项 | 内容 |
| --- | --- |
| 种子变量 | `GEN_RANDOM_SEED` |
| 默认值 | **`20260926`** |
| 作用范围 | `random.Random(cfg.random_seed)`，且 **Faker 也使用同一随机源**（`faker.seed_instance(cfg.random_seed)`） |
| 承诺 | 同一种子得到完全相同的结果（代码注释原文："确定性：同一种子得到完全相同的结果"） |

**依据**：`data-generator/src/config.py` 第 140 行；`data-generator/src/dataset.py` 第 441~448 行。

**为什么可复现很重要（要能讲出来）**：如果每次生成的数据都不同，"交易域 11458 个分钟窗口不一致 0"这个结论就无法被复核——换一台机器、换一天跑，窗口数、GMV、漏斗计数全都会变。可复现是逐窗口对账这类结论能够被第三方验证的前提。

### 4. 其他生成参数

| 参数 | 变量 | 默认值 |
| --- | --- | --- |
| 支付比例（订单中成功支付的比例） | `GEN_PAY_RATIO` | `0.92` |
| 退款比例（已支付订单中出现退款的比例） | `GEN_REFUND_RATIO` | `0.05` |
| 折扣比例（存在优惠导致 `amount != price × quantity`） | `GEN_DISCOUNT_RATIO` | `0.15` |
| 单订单最大商品件数 | `GEN_MAX_QUANTITY` | `5` |
| 批量写入大小 | `GEN_BATCH_SIZE` | `1000` |
| 行为漏斗权重 | `behavior_funnel` | VIEW 1.00 / CLICK 0.55 / CART 0.20 / FAVORITE 0.10 / BUY 0.06 |

**依据**：`data-generator/src/config.py` 第 105~144 行。

### 5. 数据规模与 ID 段

#### 5.1 规模（实测落地值）

| 数据 | 数量 | 来源 |
| --- | --- | --- |
| 用户 `user` | **1200** | `docs/sprint/SPRINT_3.md` 第 7.1 节；`docs/sprint/SPRINT_3.md` 第 2 节 |
| 商品 `product` | **600** | 同上 |
| 订单 `orders` | **6000** | 同上 |
| 支付 `payment` | **5406** | 同上（MySQL 侧；其中成功 5287、失败 119） |
| 退款 `refund` | **254** | 同上 |
| 行为事件 `behavior_event` | **20000** | `AGENTS.md` 第 15.8 节 |
| Kafka 事件合计 | **31660** | `AGENTS.md` 第 15.1 节（= 6000 + 5406 + 254 + 20000） |

#### 5.2 ID 段（生成器常量）

| 实体 | 起始 ID | 代码位置 |
| --- | --- | --- |
| 用户 `user_id` | **1001** | `data-generator/src/dataset.py` 第 34 行 |
| 商品 `product_id` | **2001** | 同文件第 35 行 |
| 订单 `order_id` | **10001** | 同文件第 36 行 |
| 支付 `payment_id` | **30001** | 同文件第 37 行 |
| 退款 `refund_id` | **40001** | 同文件第 38 行 |

#### 5.3 时间范围

| 数据 | 范围 | 来源 |
| --- | --- | --- |
| 订单时间 | `2024-10-25` ~ `2026-09-26`（约 700 天有数据） | `docs/sprint/SPRINT_3.md` 第 2 节 |
| 实时 ADS 交易窗口 | `2024-10-25 08:16:00` ~ `2026-09-26 11:37:00`（分钟级稀疏） | 同上 |
| 行为事件（离线归档） | `2024-10-02 21:53:33` → `2026-09-26 13:16:13`，覆盖 **703 个 `dt` 分区** | `docs/sprint/SPRINT_4.md` 第 10.2 节 |
| 实时侧流量窗口 | 19644 个分钟窗口，`2024-10-02 21:53:00` → `2026-09-26 13:16:00` | `docs/sprint/SPRINT_5.md` 第 2.3 节 |

### 6. 生成时强制的业务约束与时间因果

以下约束在**生成期**强制执行（不是事后校验），依据 `data-generator/src/dataset.py` 的模块头注释与各生成函数：

#### 6.1 外键与归属关系

```text
订单 user_id      必须来自 user
订单 product_id   必须来自 product
payment.order_id  必须来自 orders
refund.order_id   必须来自 orders
```

实现方式：`generate_orders()` 用 `rng.choice(users)` / `rng.choice(products)` 取值；`generate_payments()` **只对 `status = PAID` 且 `pay_time` 非空的订单**生成支付记录；`generate_refunds()` **只对支付成功的订单**生成退款记录。

#### 6.2 金额关系

```text
product.price × quantity ≈ order.amount
```

实现方式：`amount = _q2(product.price * quantity)`；若命中 `discount_ratio`（默认 15%），再乘一个 0.90~0.98 的折扣。所有金额用 `decimal.Decimal` 且 `_q2()` 四舍五入到 2 位小数——项目规范禁止用 `float` 做金额累加（`AGENTS.md` 第 4.2 节第 7 条）。

#### 6.3 时间因果链

```text
user.register_time ≤ order.create_time ≤ order.pay_time ≤ refund.refund_time
```

实现细节（这几处是防止"先退款后支付"这类无意义结果的关键）：

- 下单时间从 `max(user.register_time, product.create_time)` 起算，且不超过当前时间；
- 支付发生在上单后 1 分钟 ~ 2 天内；若 `create_time + delta > now`，则**视为未支付**（保持因果一致），而不是硬造一个未来时间；
- 退款发生在支付后 1 小时 ~ 30 天内；若超过当前时间，则截断为当前时间；
- 退款后同步把对应订单状态改为 `REFUNDED`。

#### 6.4 商品与用户属性

| 约束 | 实现 |
| --- | --- |
| `price > cost`，毛利率 15% ~ 60% | `margin = uniform(0.15, 0.60)`，`cost = price × (1 − margin)` |
| 类目与价格区间匹配 | 10 个类目各带价格区间（如手机数码 899~8999、图书文娱 15~299） |
| 95% 商品在架 | `status = 1 if rng.random() < 0.95 else 0` |
| 用户等级/性别/支付方式/设备按权重分布 | 各自的 `*_WEIGHTS` 常量 |
| 省份与城市匹配 | `PROVINCE_CITIES` 映射（20 个省级行政区） |

#### 6.5 行为漏斗

项目要求在生成期保证逐级收窄：

```text
count(VIEW) > count(CLICK) > count(CART) > count(BUY)
```

实测落地值：**VIEW 10472 > CLICK 5759 > CART 2095 > BUY 628**（FAVORITE 1046 为旁支，符合"显著少于 VIEW"）（`AGENTS.md` 第 15.8 节）。

#### 6.6 事件与业务库的一致性（**最关键的一条**）

`generate_events` **不重新随机生成一套订单**，而是读取
`data-generator/state/dataset_snapshot.json` —— 那是 `generate_mysql_data` **实际写入 MySQL 的那批数据**。

```text
Kafka 事件里的 order_id / payment_id / refund_id / amount
        ↓ 必然对得上
MySQL 里真实存在的记录
```

**代价（有意设计）**：`generate_events` **必须**在 `generate_mysql_data` 之后运行。项目明确规定**不允许**产生与业务库不一致的事件。

**依据**：`docs/data-source-design.md` 第 4.3 节；`data-generator/src/dataset.py` 的快照持久化段（`SNAPSHOT_VERSION = 1`）。

### 7. 为什么"可复现 + 满足约束"才支撑得起逐窗口对账

把这条逻辑讲完整（答辩时这句话是加分点）：

```text
逐窗口对账要求：同一批业务事实，在实时侧（Kafka 事件）与离线侧（MySQL 业务行）
                必须逐条可对应、且每次重跑得到相同的结果
        ↓
因此需要两件事：
  ① 事件与业务行一一对应  → 由 dataset_snapshot.json 传递保证（不是各随机一套）
  ② 结果可复现            → 由 GEN_RANDOM_SEED 保证（同种子结果完全相同）
        ↓
两条都满足，才有资格谈"11458 个窗口不一致 0"是一个可复核的结论，
而不是一次偶然跑出来的漂亮数字
```

**依据**：`docs/data-source-design.md` 第 4.3、6 节；`docs/sprint/SPRINT_3.md` 第 3.2 节。

### 8. 数据可靠性保证（分层）

仓库中已有一张完整的"可靠性保证"表，可直接引用：

| 层次 | 手段 | 落地位置 |
| --- | --- | --- |
| 生成期 | 外键关系、金额一致性、时间因果 | `data-generator/src/dataset.py` |
| 生成期 | 同种子可复现 | `GEN_RANDOM_SEED` |
| 入库期 | 外键约束（MySQL FOREIGN KEY） | `sql/mysql/01_schema.sql` |
| 入库期 | 写入后连表校验 | `generate_mysql_data` 的 `verify()` |
| 测试期 | 24 个单元测试断言业务关系 | `tests/test_data_generator.py` |
| 测试期 | 74 个冒烟测试断言落地内容 | `tests/smoke/test_infrastructure.py`(27) + `test_realtime.py`(47) |
| 运行期 | 11 项健康检查（含实时链路 6 项） | `scripts/health-check.sh` |
| 运行期 | 事件与业务库一一对应 | `state/dataset_snapshot.json` |
| 运行期 | 指标与 MySQL 精确对账 | `scripts/verify-sprint-1.sh` 第 7 步 |
| 治理期 | 数据质量规则（完整性 / 一致性 / 及时性） | Sprint 11（已交付，25 条校验） |

**依据**：`docs/data-source-design.md` 第 6 节（本表最后一行按 Sprint 11 实际交付情况更新）。

### 9. 数据来源相关的已知局限（来自数据来源设计文件本身）

| 局限 | 说明 | 现状 |
| --- | --- | --- |
| 生成器是"业务系统替身" | 没有真实电商后台，生成器同时扮演业务写入方与埋点方 | 如实披露 |
| 无 CDC | MySQL → Kafka 靠生成器双写，未用 Flink CDC / Canal | 未做 |
| 单机单副本 | Kafka 副本 1、Doris 单 BE，无高可用 | 明确为开发环境 |
| 行为事件不落 MySQL | 仅存在于 Kafka，由 Spark 归档到湖仓 | 已由 Sprint 4 归档（20000 行；**本次验收窗口内未观察到丢失** —— 判据是归档作业内自检"归档行数 == Kafka 消息数"，单次窗口内的等式、不覆盖之后的增量事件，且 Kafka 副本数为 1）。另注意：**表内行数 = distinct `event_id` 基数 ≠ 链路处理量**（该 topic latest 合计 580000 = 29 × 20000，Doris 侧 UNIQUE KEY upsert 去重后才是 20000） |
| **重复生产事件会让窗口指标累加** | Flink source 用 `earliest-offset` 可重放历史；若把同一批事件重复写进源 topic，窗口聚合会把它们累加（实测 PV 翻倍） | 验收用 `--replay` 构造"恰好一代事件"；根治方案是按 `event_id` 去重，未实施 |
| 无数据血缘 | 表级血缘尚未采集（RAG 层提供了表结构与口径的检索，但不是列级血缘） | 部分覆盖 |

**依据**：`docs/data-source-design.md` 第 7 节；`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md` 第 5、6 节。

> ⚠️ **一条必须能讲清楚的坑**：重复生产事件会让窗口指标累加，这一点在 Sprint 1 实测出现过（PV 合计 39987 vs 事件 20000，近 2 倍），根因是 **SQL Gateway 为 session 模式，重启 `flink-jobs` 容器不会取消旧作业**，旧作业把重放事件累加到了旧窗口状态。修法是新增 `scripts/cancel-flink-jobs.sh` 并在提交脚本启动时自动清场，健康检查增加"Flink 作业唯一性"检查项（`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md` 第 5 节）。

---

## 第二部分 · 已知限制汇总

> 组织方式：**影响 / 现状 / 计划** 三列。
> 分组说明：A 组是仓库《决策记录》第三节原始登记的 8 条（其中 2 条已关闭，保留在表中并标注）；B 组是各 Sprint 文档中记录、但未进入该表的限制；C 组是已修复但值得作为"历史缺口"记住的项；D 组是未采集/未做的测量项。

### A 组：原始《决策记录》第三节登记的限制

| # | 限制 | 影响 | 现状 | 计划 | 来源 |
| --- | --- | --- | --- | --- | --- |
| A1 | **接口无鉴权** | 知道 IP 就能读指标 | 全部接口**只读**；写操作被守卫与只读账号两层拒绝 | 后续加 Token；演示环境可接受 | `docs/DECISIONS.md` 第三节 1 |
| A2 | **Airflow SimpleAuthManager 仅供开发** | 官方源码明写 *"should not be used in production... only intended for development and testing"*；口令明文存储、首次初始化会打印到日志、**无轮换机制** | 演示阶段维持现状（已绑回环 + 需登录 + 未开放额外端口）；口令文件为明文 JSON、`0600`、仅 root 可读 | 装 `apache-airflow-providers-fab` 换成 FAB auth manager，或前置 Keycloak | `docs/DECISIONS.md` ⏳3 |
| A3 | **明文 HTTP** | 传输不加密（可被中间设备改写——这正是当初偶发 502 的机制） | `.env` 的 `SITE_SCHEME=http`；443 不监听；证书仍在机器上但已不被引用 | 注册域名 + 申请证书 + 完成备案后启用（`setup-tls.sh` + 按 commit `347dfdd` 加回 443 段 + 改 `SITE_SCHEME`） | `docs/DECISIONS.md` ⏳2；`AGENTS.md` 第 15.7 节 |
| A4 | **MinIO 使用 root 凭据 + 密钥对 `ps` 可见** | 不是最小权限；密钥通过 `spark-submit --conf` 传递，**任何能登录的用户 `ps` 就能看到明文** | 未做 | 建只对 `lakehouse` 桶有读写权的专用用户；改用 `spark.hadoop.fs.s3a.credential.provider` 或环境变量注入 | `docs/DECISIONS.md` ⏳4 |
| A5 | **流量域离线指标**（原缺口） | 离线链路只覆盖交易域，流量域无法对账 | ✅ **已关闭**：Sprint 4 完成归档（20000 行 == Kafka latest offset 合计、703 个 `dt` 分区、16/16 自检全过）；Sprint 5 完成 DWD/DWS/ADS 分层与逐窗口对账（19643 个窗口不一致 0） | — | `docs/DECISIONS.md` ⏳5、⏳6；`AGENTS.md` 第 15.8、15.12 节 |
| A6 | **Doris FE 单点** | 查询层无高可用 | 开发环境定位 | 论文中说明生产部署差异 | `docs/DECISIONS.md` 第三节 6 |
| A7 | **磁盘无冗余、无异地备份** | 数据丢失风险 | 未做 | 论文说明 | `docs/DECISIONS.md` 第三节 7 |
| A8 | **Airflow 与实时链路抢内存** | 批处理期间必须暂停实时链路（看板曲线延后追上） | 已用错峰模式 + 内存闸门（阈值 3000 MB）；闸门实测拒绝过一次批处理 | 生产应分离节点 | `docs/DECISIONS.md` 第三节 8；`AGENTS.md` 第 15.8 节 |

### B 组：各 Sprint 记录、未进入上表的限制

| # | 限制 | 影响 | 现状 | 计划 | 来源 |
| --- | --- | --- | --- | --- | --- |
| B1 | **数据全部为合成数据** | 不代表真实业务分布；无法验证模型在真实分布下的表现 | 生成器可复现（种子 `20260926`），业务约束生成期强制 | 接真实数据源需引入 CDC | `docs/data-source-design.md` 第 7 节 |
| B2 | **实时侧 1 个窗口的 `click_rate` 与自身计数矛盾** | 不影响任何已发布口径的指标数值（7 个判据列在 19643 个窗口全部一致），但会让"批流对账零差异"这句话不严谨 | **已定位、判据已改严、证据已落盘，经成本评估后决定不修**：① 用纯算术 `1/2 = 0.5` 这个第三方判据定责 → 离线侧自洽、实时侧 1 个矛盾窗口；② 判据从"两侧比率相等"改为"每侧比率 == 按该侧自身计数重算"；③ `ads_reconcile_traffic_summary.realtime_rate_anomaly_windows = 1` 作为一等结论落盘 + 逐窗口标记。**定性上这是"已定位并留证的真实缺陷"，不是"未解决"，也不是"环境问题"。** 机制层面原因标注为待确认（Flink SQL Gateway REST 未响应、`sql-client.sh` 在本机依赖配置下会挂住，未能单独隔离验证） | **不走方案 A**：A（重放实时链路）与 C（改 Flink 作业后重放）都会触发 Kafka 全量重放、覆盖实时侧 19644 个窗口——**重放成本远大于该缺陷影响**；方案 B（只改 SQL 不重部署）已被否决：会造成仓库代码与运行作业不一致，比不改更危险。项目负责人已复核维持此结论 | `docs/sprint/SPRINT_5.md` 第 9.3、10 节；`docs/DECISIONS.md` ⏳10；`sql/metadata/metrics.md` 第 3.3 节 |
| B3 | **结构债：两份阶段 `case` 重复** | 新增阶段要改两处；只改一处会出现"一个入口能跑、另一个静默跳过"（Sprint 4 已因此踩坑） | **未做重构，且是有意的**。两道现有防线：两份 `case` **都**有"兜底分支必须失败"的写法 → 漏加分支会**大声失败**，不会报告成功。因此定性为**可维护性债**而非正确性缺陷 | 方案 A（让 `run-batch-pipeline.sh` 委托 `submit-offline-job.sh`，并把 `load` 补进后者的 `KNOWN_STAGES`）需**一次性完成**；方案 B（只读的防漂移断言，零行为变更）可立即做。建议先做 B | `docs/sprint/SPRINT_12.md` 第 4.1~4.3 节 |
| B4 | **查询审计未落盘** | `steps` 随响应返回但未持久化 | 未做 | MCP / 可观测性阶段补 | `docs/sprint/SPRINT_7.md` 第 10 节 |
| B5 | **Agent 无多轮对话记忆、无 checkpoint** | 每次提问独立；前端不保存历史 | 单次提问即一次图执行（有意边界） | 会话状态属后续阶段 | `docs/sprint/SPRINT_7.md` 第 10 节；`SPRINT_8.md` 第 8 节 |
| B6 | **Agent 无成本核算** | 只限制了轮次与 token 上限，未统计每次提问花费 | 未做 | 可观测性范畴 | `docs/sprint/SPRINT_7.md` 第 10 节 |
| B7 | **`information_schema` 在授权表集合内** | Agent 可以主动探测元数据。它让"表是否存在"这类问题回答更可靠（实测问「dwd_user_profile 有哪些字段」就是靠它先确认表不存在再如实回答），但也意味着 Agent 能做"库表结构探测"这类动作 | 未改动该授权集合。MCP 的 `tables_lookup` 走服务自己拼的元数据查询、**不接受用户传表名**，因此**经 MCP 无法做任意元数据探测**（能力面比直连 `/query` 更窄） | 与 B8 一并复核（"数据服务侧权限边界复核"） | `docs/sprint/SPRINT_9.md` 第 9.6 节；`SPRINT_10.md` 第 9.3 节 |
| B8 | **`dim_product` / `dim_user` 曾在 Doris 里长期为 0 行** | 任何 `JOIN dim_product` / `JOIN dim_user` 的查询**不报错**，返回 HTTP 200 + `row_count=0`——**静默错误比报错更危险**；Agent 会拿"0 行"当"真的没有数据"来回答 | ✅ **已修并已服务器验证**（提交 `b195d19` + 通配修正）：`--stage load` 后 **12 张表行数全部通过**（`dim_product 600 == 湖仓 600`、`dim_user 1200 == 湖仓 1200`）；经只读服务查那条曾恒为 0 行的 JOIN → 返回 10 行真实类目 GMV（电脑办公 15,457,835.79），`source.tables` 正确列出两张表 | 已修复，无遗留 | `docs/DECISIONS.md` ⏳11；`docs/PERFORMANCE.md` 第 5 节 |
| B9 | **数据服务把 SQL 语句错误也报成 503** | Doris 的语句错误（如 `Unknown column 'window_start'`）被统一包成 **503 `DORIS_UNAVAILABLE`**；Agent 会把"我 SQL 写错了"误读成"仓库不可用"，于是多跑一轮重试；排障时也容易把语义错误当成基础设施故障 | 未改（属 `services/api`，跨 Sprint 边界） | 建议区分 **400**（客户端错误，重试无用，应把错误原文交给模型去改）与 **503**（真正的连接/超时故障） | `docs/DECISIONS.md` ⏳8 |
| B10 | **Doris 里有一张无 DDL 来源的 `ecommerce.test_connection`** | 违反项目规范"禁止手工改容器内数据库而不落盘 `sql/`"；留着会让人以为它是平台的一部分 | 未删（属他人文件范围） | 建议**删掉**而不是补 DDL——它没有业务含义 | `docs/DECISIONS.md` ⏳9；`SPRINT_8.md` 第 9.7 节 |
| B11 | **文档与代码的白名单数量曾不一致** | `sqlguard.BUSINESS_TABLES` 实测 16 张，而 `AGENTS.md` 第 15.6 节与 `SPRINT_7.md` 曾写作"19 张表" | 已以**代码为准**（16 张）并在文档中标注差异来源 | 改白名单时必须**同步**三处：`sqlguard.py`、Agent 系统提示词的「数据地图」、文档 | `AGENTS.md` 第 15.6 节；`SPRINT_8.md` 第 9.7 节 |
| B12 | **归档阶段可能存在"静默空转"** | 一次人工触发的 run 中 `archive_behavior` 仅耗时 **0.6 s**，而主线那次是 **363.3 s**，相差 600 倍；该 run 所有任务状态都是 `success`，**只能靠耗时异常发现** | **只登记、未下结论**（这是 Sprint 11 实施者按主控指示代记的待复核项） | 复核该次 run 的归档自检输出：`Kafka 实际消息数` 是否 > 0、`可解析行数 == Kafka 消息数` 是否成立、`dt` 分区数是否与 `ods_behavior_entry` 行数匹配。**判据以行数与分区数为准，不以"阶段返回成功 + 耗时短"为准** | `docs/PERFORMANCE.md` 第 3.2 节；`docs/sprint/SPRINT_12.md` 第 8.8 节 |
| B13 | **湖仓层间校验的表明是写死的** | `dwd_ods_parity_all.sql` 里的表名写死（`spark-sql -e` 不支持参数化），湖仓新增表时必须同步改那份 SQL 的 IN 列表，**否则新表会静默不参与层间校验**；当前只覆盖 5 张交易表 | 该注意事项已写在 SQL 注释里 | 改为从 Metastore 动态取表清单 | `docs/sprint/SPRINT_11.md` 第 10 节 3 |
| B14 | **无告警** | Prometheus 只做抓取与展示，问题不会主动通知 | 阈值判断刻意留在 `infrastructure/quality/` 的**可审计 SQL** 里 | 优先用 Grafana 的 alerting（数据源已就绪） | `docs/sprint/SPRINT_11.md` 第 10 节 1 |
| B15 | **未部署 cadvisor / node-exporter / alertmanager** | 无容器级指标的长期采集 | 512 MB 监控预算塞不下（Prometheus 256m + Grafana 256m 已是上限）；容器级内存改用 `docker stats --no-stream` 取证 | 取证方式可换，预算不放宽 | `docs/sprint/SPRINT_11.md` 第 5 节 |
| B16 | **Grafana 面板默认时间范围 `now-90d`** | 本机数据是一次性生成的历史数据，默认时间窗会让人觉得"面板是空的" | 已写在面板顶部的说明里 | — | `docs/sprint/SPRINT_11.md` 第 10 节 4 |
| B17 | **服务层直装宿主机** | 换机器时要多跑几个安装脚本 | 已全部脚本化并保证幂等 | — | `docs/DECISIONS.md` D1 |
| B18 | **湖仓存储由 HDFS 改为 MinIO/S3A** | 与原始设计文档的表述不一致 | 原因是**内存不足**（原设计写 HDFS） | 已在 AGENTS 与 Sprint 文档中标注偏差 | `AGENTS.md` 第 15 节 Sprint 2 行；`docs/DEVELOPMENT_LOG.md` 第 382 行 |
| B19 | **Iceberg DDL 未落盘 `sql/iceberg/`** | 与项目规范"所有 DDL 必须存入 `sql/`"有偏差 | 改为**从源表 schema 推导**，理由是手写 18 张表的 DDL 会与源表定义分叉，而分叉只在迁移后才发现；Iceberg 表的权威定义在 Metastore 里，`SHOW CREATE TABLE` 随时可取 | 已在文档中记录偏差 | `docs/sprint/SPRINT_5.md` 第 8.4 节 3 |
| B20 | **无数据血缘（列级）** | 表级血缘尚未采集 | 已提供表结构与口径的检索（RAG 语料含 16 张表结构） | — | `docs/data-source-design.md` 第 7 节 |

### C 组：已修复，但值得作为"历史缺口"记住的项

> 答辩时若被问"你们有没有发现过自己的安全问题/数据问题"，这一组就是答案——**发现问题、修掉、并用测试锁住**。

| # | 历史缺口 | 影响 | 现状 | 计划 | 来源 |
| --- | --- | --- | --- | --- | --- |
| C1 | **sqlguard 的 UNION 元数据探测缺口** | `_FORBIDDEN_PATTERNS` 的 `METADATA_PROBE` 正则按**语序**匹配，只覆盖"元数据写在 UNION 后面"那一支。把 `information_schema` 写在**前面**（`SELECT table_name FROM information_schema.tables UNION ALL SELECT gmv FROM 业务表`）会被**放行**。两张表都在授权集合里，**没有越权数据**，但它是一次"未授权元数据探测"的入口，与规范表述不一致；更根本的是——**判据一旦写成"按顺序匹配"，同一件事换个语序就绕过去了，这类"看起来还在防"的规则比没有规则更危险** | ✅ **已修**：改成**顺序无关**的组合判据——只要同时出现 `union` 与 `information_schema` 就拒绝（lookahead，不消耗字符）；`information_schema` **单独**查询仍然允许（Agent 问"有没有这张表"要走它）。本地用纯 Python 对五种语句逐一比对新旧行为：反序 UNION 两种写法**旧版放行、新版拦截**；正序 UNION 两版都拦截（**无回退**）；单独查 `information_schema` 与普通业务查询两版都放行（**无过度拦截**）。测试按 `xfail(strict=True)` 的设计意图摘掉标记并改成正式的拒绝断言 | 服务器侧 `pytest tests/test_sql_guard_adversarial.py` 与接口对抗用例**待跑** | `docs/DECISIONS.md` ⏳13；`docs/sprint/SPRINT_12.md` 第 2.2.2 节 |
| C2 | **装载脚本"先清空后装载"** | `load-batch-to-doris.sh` 逐表**先 `TRUNCATE TABLE`，再 `INSERT ... FROM S3(...)`**，且在清空之前不校验 S3 侧是否真的可读。在 `hive-metastore` 崩溃期间跑过一次 → **表被清空、数据没装回来** → Doris `lakehouse_ads` 全空。**症状出现在 Doris，根因在湖仓侧**。`TRUNCATE` 与随后的 `INSERT` 虽然"各自原子"，但**两者之间不是原子的**——一旦第二步失败，系统停在一个**比失败前更差的状态**：不是"装载没更新"，而是"数据没了"。对**服务层**尤其致命（它会让"一次失败的批处理"升级成"对外服务没有数据"） | ✅ **已修**（提交 `b195d19`）：`TRUNCATE` 之前先探测源端（S3 路径可读、且至少一行），不满足就**保留现有数据并失败退出**（`exit 1`），而不是先把数据删掉再试 | 服务器验收已跑（`--stage load` 后逐表行数与湖仓一致，`ads_traffic_1m 19644 == 湖仓`） | `docs/DECISIONS.md` ⏳12；`docs/sprint/SPRINT_11.md` 第 10.3 节 |
| C3 | **`.env` 权限被收紧成 600 导致 Agent 起不来** | `deploy-monitoring.sh` 会把 `.env` 收紧到 600，而 `Group=dpagent` 的服务需要**组读**（640）。**故障现场与改动现场不在一起**（改的是监控部署脚本，坏掉的是 Agent），且只在"下次部署时"发作。**是最难关联的一类故障** | ✅ 已把 `.env` 恢复为 `640 root:dpapi` 并重启服务；并把"每次部署都校正一次权限"写进硬规范（权限属于"别人也依赖的共享状态"） | 该脚本本身应改成 `chmod 640` + `chown root:dpapi`（与 `install-web.sh` 一致）；**本次未改他人脚本** | `AGENTS.md` 第 15.11 节硬规范 1；`SPRINT_8.md` 第 9.7 节；`SPRINT_11.md` 第 7.12 节 |
| C4 | **原子换树同步删掉机器状态** | `.env`、三个 venv、`airflow/airflow.env`、三个挂载 JAR 被删；`docker compose` 直接不可用、Agent 起不来、Airflow 三单元"活着但重启即失败" | ✅ 已恢复（三个 JAR 与镜像内原件做过 md5 比对，全部 MATCH）；同步脚本已改名禁用（保留原文供复核） | 已沉淀为硬规范：禁止原子换树、禁止在凭据落盘前重建容器、共享编排文件先落副本再同步 | `AGENTS.md` 第 15.11 节；`docs/sprint/SPRINT_12.md` 第 8.2 节 |
| C5 | **测试依赖从未进过任何 requirements** | 重建 venv 时 `pytest` / `Faker` / `python-dotenv` 全部丢失，全量测试跑不起来，症状是"测试大面积失败"（很容易被误判成代码问题）。**缺失的依赖只在低频操作（重建 venv、灾难恢复）上暴露** | ✅ 已新建 `requirements-dev.txt` 正式声明三者并写清用法 | 已修复，无遗留 | `docs/sprint/SPRINT_12.md` 第 8.5 节 |
| C6 | **`deploy/nginx/data-platform.conf` 被同步静默回滚** | Sprint 11 往站点配置加了 `/grafana/` 与 `/metrics/` 两个 location，`nginx -t` 通过并 reload；随后一次包含 `deploy/` 目录的同步把工作区里**旧版**配置推到服务器并覆盖，两个 location 直接消失（`/grafana/` 变 404）。**根因不是谁操作失误**，而是"创作副本 → 工作区 → 服务器"这条链的结构：服务器上如果有比工作区更新的仓库文件，下一次同步就是一次**静默回滚** | ✅ 已恢复并成为硬规范：改共享编排文件必须先落创作副本 → 同步工作区 → 再同步服务器；验收前必须核对 `/etc/nginx/sites-available/data-platform.conf` 与仓库副本的 **md5 一致**（实测 `b5197e5121e08d689daebb72d3e626e2`） | 已修复，无遗留 | `docs/sprint/SPRINT_11.md` 第 7.8 节；`AGENTS.md` 第 15.11 节硬规范 4 |
| C7 | **Flink 因 session 模式重复累加已验收脚本读到的数** | `flink-jobs` 容器重启不会取消旧作业，旧作业把重放事件累加到旧窗口状态，实测 PV 合计 39987 vs 事件 20000（近 2 倍） | ✅ 已修：新增 `scripts/cancel-flink-jobs.sh`，提交脚本启动时自动清场；健康检查增加「Flink 作业唯一性」检查项 | 已修复，无遗留 | `docs/sprint/SPRINT_1_VERIFICATION_STATUS.md` 第 5 节 |

### D 组：未采集 / 未做的测量项

> 这一组不是缺陷，而是**明确写下"没测"以避免被误认为有结论**。

| # | 项目 | 为什么没做 | 现状 |
| --- | --- | --- | --- |
| D1 | **并发 / 压力测试** | 本机内存不允许（实时链路常驻约 12~13.7 GB，性能采集时可用仅 3921 MB）。要做必须另开一台机器 | 未采集 |
| D2 | **公网链路延迟（含 nginx 一跳）** | 与现有的回环口径不同，需单独测并说明第 15.7 节记录的中间设备改写问题 | 未采集。现有全部性能数字均为**本机回环**口径，**不代表**用户从浏览器访问的体验 |
| D3 | **Iceberg 与 Doris 内表的查询延迟对比** | 高价值（Sprint 5 已建 Iceberg 副本），但需要 Spark 会话，会与实时链路抢内存 | 未采集 |
| D4 | **性能优化本身** | 本阶段的交付是**基线**（测量与文档），不是优化。**没有基线就谈优化，等于把"感觉快了"当结论** | 未做 |
| D5 | **实时 vs 离线在大数据量下的延迟对比** | 两侧数据量在这两个聚合上同量级（实时 11459 行 / 离线 626 行），都在单 BE 内存/本地盘上，未到读放大的量级 | 如实记录为"**无显著差异**"，**不编造趋势** |
| D6 | **LLM 内部推理耗时的分解** | LLM 调用是外部服务，其延迟只能整体测、不能分解，且受网络与服务端负载影响 | 文档给出的"规划 + 汇总 ≈ 19169 ms"是**差值**（总量 − 检索 − 取数），**不是**对模型内部推理时间的测量。**不得**表述为"模型推理耗时" |
| D7 | **`click_rate` 缺陷的机制层面原因** | Flink SQL Gateway 的 REST 接口未响应，`sql-client.sh` 在本机依赖配置下会挂住，未能单独隔离验证 | "这一行的比率与它自己的计数矛盾"是**实测事实**；机制标注为**待确认**（不依赖机制解释） |

### E 组：同名不同范围的数字（写作与答辩时必须带范围）

> 这一组是**整理报告时逐字核对数字来源发现的**。它们不是缺陷，但属于最容易在答辩上被追问、也最容易在改写时被"顺手统一"掉的类型。处理原则与 Iceberg 的 `60/60` 与 `70/70` 一致：**保留各自出处并写明范围，不合并、不取平均、不为"看起来一致"而丢掉一次真实验收。**

| # | 同名数字 | 两处取值与范围 | 本文的处理 | 来源 |
| --- | --- | --- | --- | --- |
| E1 | **Iceberg 迁移校验计数** | **60/60**（范围 **18 张表**，阶段 4 首次成功迁移）/ **70/70**（范围 **23 张表**，流量域建成后复跑） | **两个都保留**，在正文 5.4.1 用一张范围对照表 + 一句判据构成说明写清楚；`70/70` 无逐项细目，只作口径解释不作实测细目 | `docs/sprint/SPRINT_5.md` §8.1（60/60）；`AGENTS.md` §15 Sprint 5 行（70/70） |
| E2 | **`dwd_traffic_behavior_detail` 行数** | **已定性：当前值 = 20000**（两条独立只读观测：① Kafka 源 topic `behavior_event` latest 合计 = 6506+6592+6902 = **20000**，earliest 全 0、未被 retention 清理；② Doris `COUNT(*)` = 20000 且 `COUNT(DISTINCT event_id)` = 20000，两次观测一致）。**40000** 的唯一出处是 `SPRINT_3.md:52`（§2 事实核查表，注明"与 MySQL 1:1"），**无观测时间**，且流量域在 MySQL **没有源表**（这正是 Sprint 3 自述、Sprint 4 才归档补齐的缺口）→ 判为**当时的观测误差**，该行已改写并加审计注 | **历史侧状态保留为 UNRESOLVED**：2026-09-26 13:18 topic 重建**之前**的真实状态已不可考（需 Kafka topic 重建历史或 Doris 审计日志，当前不可得）—— 这是"来源不可考"，**不是"两种口径都对"**。**关键：不影响对账结论** —— 流量域对账判据是分钟窗口的 `uv`/`pv`/5 个行为计数，不是该 DWD 的行数。**行数口径**：表内行数 = distinct `event_id` 基数 ≠ 链路处理量（topic latest 合计 580000 = 29 × 20000） | `.tmp/audit-traffic-20000-40000.md`；`docs/sprint/SPRINT_1_VERIFICATION_STATUS.md` §2；`docs/sprint/SPRINT_3.md` §2（含审计注） |
| E3 | **`dws_trade_user_1d` 行数** | **5851**（`SPRINT_3.md` §7.1 验收结果）/ **5883**（`SPRINT_5.md` §2.3 迁移基线 **与** §8.1 迁移后逐表行数——较晚且两处一致） | 5.2 节行数表按原文保留 5851 并标注差异；5.4 节引用 Iceberg 迁移结果时采用 **5883**。**两者都不参与任何对账判据** | `docs/sprint/SPRINT_3.md` §7.1；`docs/sprint/SPRINT_5.md` §2.3、§8.1 |
| E4 | **实时链路常驻内存** | **13.7 GB**（容量规划与闸门设计依据的常驻口径，反复出现于 `SPRINT_3/9/11/12` 与 `DECISIONS` D11）/ **约 12 GB**（`PERFORMANCE.md` §0.1 采集现场的单次快照） | 引用时**必须带口径**：说 13.7 GB 时注明是常驻口径，说 12 GB 时注明是 2026-09-27 采集现场快照 | `AGENTS.md` §15.5；`docs/PERFORMANCE.md` §0.1 |
| E5 | **质量校验清单条数** | **25**（`checks.conf` 的 `[check.*]` 段**实数 25**，已逐段点算确认；`AGENTS.md`、`SPRINT_11.md` §4.1、`PROJECT_DESIGN_V1` 相关表述一致）/ 文字"**21**"（`SPRINT_11.md` §7.11 记录"正则收窄后为 21"） | **本文采用 25**（以 `checks.conf` 实际段数为准）。`21` 是 Sprint 11 自身的一处口径注记（该节讲的是"正则太宽会数出多余的校验项"），**属仓库内部注记不一致，不影响交付物**；建议由数据质量负责人复核 §7.11 的这处表述 | `infrastructure/quality/checks.conf`（实数 25）；`docs/sprint/SPRINT_11.md` §7.11 |
| E6 | **pytest 计数** | **353/0/0/3 xfail**（Sprint 12 终局全量）/ **49/49**（Sprint 7 验收项）/ **71/0/0**（Sprint 8）/ **59/0/0**（Sprint 9）/ **84/0/0**（Sprint 10）/ **61/0/0**（Sprint 11）/ **32/0/0**（Sprint 12）/ **172 passed**（Sprint 3 当时全量）/ 单元测试 **24**（Sprint 0）/ **74**（Sprint 1 冒烟）/ **55 → 74**（Sprint 6 单元） | 全部**按各自范围**引用，绝不用其中一个代表"测试通过数"。特别注意：`verify-sprint-N.sh` 的输出是**验收项计数**，`pytest` 的输出是**用例计数**，两者量纲不同 | `AGENTS.md` §15 各节；`docs/sprint/SPRINT_12.md` §8.3 |


---

## 第三部分 · 答辩时的三条固定话术

### 话术一：被问"数据是真实的吗"

> 是程序生成的合成数据。生成器有随机种子，默认 20260926，同一个种子能生成完全一样的数据；外键关系、金额关系、时间因果这些业务约束都在生成期强制保证；而且 Kafka 事件是基于真实写进 MySQL 的那批数据生成的，不是各随机一套。**它不代表真实的业务分布**，这是最大的局限。不过正因为可复现、且事件与业务行一一对应，才支撑得起逐窗口对账——否则"11458 个窗口不一致 0"这个结论换台机器就跑不出来了。

### 话术二：被问"有什么不足"

> 三条。第一，数据是合成的，不代表真实分布。第二，实时侧有一个窗口的比率列和它自己的计数矛盾——我定位到了、离线侧是对的，但**我没有修**，因为修它要重部署 Flink 作业并触发 Kafka 全量重放，覆盖实时侧一万九千多个窗口；为一个小数点后的显示问题承担这个风险不划算。我的处置是把判据改严、把矛盾窗口数落盘成证据，**没有加容差也没有删证据**。第三，有一处结构债，两个脚本各有一份相同的阶段分发表——它是可维护性债而不是正确性缺陷，因为两份都有"兜底分支必须失败"的写法，漏加分支会大声失败。完整的限制表在报告的 7.3 节。

### 话术三：被问"怎么证明你的结果是真的"

> 每个结论我都整理成"判据是什么、实测值多少、怎么复现"。举一个最有说服力的：质量校验脚本声称"校验失败会退出码 1"，但这句话靠读代码证明不了——一个无论结果都返回 0 的脚本，读起来也可能很像对的。所以我做了一个可执行的证明：故意把两条校验的期望值改成不可能成立的值，断言必须返回退出码 1，实测两次都是 1，然后**再跑一次正常校验证明失败来自断言而不是数据被改坏**。**一个从来没红过的断言，等于没有断言。**

---

## 附：数字 → 来源 快查表

| 数字 | 含义 | 来源文件 |
| --- | --- | --- |
| 1200 / 600 / 6000 / 5406 / 254 | 用户 / 商品 / 订单 / 支付 / 退款 | `docs/sprint/SPRINT_3.md` 第 7.1 节 |
| 20000 | 行为事件数 | `AGENTS.md` 第 15.8 节 |
| 31660 | Kafka 事件合计 | `AGENTS.md` 第 15.1 节 |
| 20260926 | 默认随机种子 | `data-generator/src/config.py` 第 140 行 |
| 1001 / 2001 / 10001 / 30001 / 40001 | 五类 ID 起始值 | `data-generator/src/dataset.py` 第 34~38 行 |
| 11458 / 0 | 交易域对账窗口数 / 不一致数 | `docs/sprint/SPRINT_3.md` 第 7 节 |
| 19643 / 0 | 流量域对账窗口数 / 不一致数 | `docs/sprint/SPRINT_5.md` 第 9.2 节 |
| 51,890,375.77 | GMV（两侧相等，精确到分） | `docs/sprint/SPRINT_3.md` 第 7.2 节 |
| 5287 / 45,615,110.02；119；254 / 1,825,511.73 | 支付成功笔数与金额 / 支付失败笔数 / 退款笔数与金额 | 同上 |
| 23；**60/60**（范围 18 张表）；**70/70**（范围 23 张表） | Iceberg 迁移表数 / 阶段 4 首次迁移校验 / 流量域建成后复跑校验 | `docs/sprint/SPRINT_5.md` 第 8.1、9.6 节（60/60）；`AGENTS.md` 第 15 节 Sprint 5 行（70/70）。⚠️ **两者范围不同、不可混读**：60/60 那次只有 18 张表 |
| 1 | 实时侧比率列矛盾窗口数 | `docs/sprint/SPRINT_5.md` 第 9.3 节 |
| 10472 / 5759 / 2095 / 628 / 1046 | 漏斗 VIEW / CLICK / CART / BUY / FAVORITE | `AGENTS.md` 第 15.8 节 |
| 19999 / 1200 | `SUM(19644 窗口的 uv)` / 全部事件去重用户数 | `sql/metadata/metrics.md` 第 3.1 节 |
| 3000 MB / 1.65 GB / 2258 MB | 闸门阈值 / 暂停释放量 / 被拒时的可用量 | `docs/DECISIONS.md` D11；`AGENTS.md` 第 15.8 节 |
| 13.7 GB / 16 GB | 实时链路常驻 / 机器总内存 | `AGENTS.md` 第 15.5 节。⚠️ **另一处写 12 GB**（`docs/PERFORMANCE.md` 第 0.1 节采集现场"8 个 Flink sink 作业 RUNNING（常驻约 12 GB）"）——**两者范围不同**：13.7 GB 是容量规划与闸门设计所依据的**常驻口径**（反复出现在 `SPRINT_3/9/11/12` 与 `DECISIONS` D11），12 GB 是 2026-09-27 09:11 那次采集时的**现场单次快照**。引用时必须带上"是哪个口径"，不要混用 |
| 8.5 / 8.3 / 11.7 / 53.8 ms | 只读接口点查 / 聚合 / 关联 / overview（n=7 中位数） | `docs/PERFORMANCE.md` 第 1.2 节 |
| 7.1 / 7.1 ms | 实时与离线同口径聚合延迟 | `docs/PERFORMANCE.md` 第 2 节 |
| 1992.3 s / 约 81 分钟 / 149.8 s | 批量作业 9 任务合计 / 端到端 / 错峰固定成本 | `docs/PERFORMANCE.md` 第 3.1、3.3 节 |
| 19187.5 ms / 18.3 ms / 99.9% | Agent 端到端中位数 / 数据侧耗时 / LLM 占比 | `docs/PERFORMANCE.md` 第 4.2 节 |
| 353 / 0 / 0 / 3 | 全量 pytest 通过 / 失败 / 跳过 / xfail | `docs/sprint/SPRINT_12.md` 第 8.3 节 |
| 61/0/0；84/0/0；59/0/0；71/0/0；49/49；40/0/0；32/0/0 | 各 Sprint 验收结果 | `AGENTS.md` 第 15 章 |
| 512m / 105 MiB / 187 MiB | 监控内存预算 / Prometheus 实测 / Grafana 实测 | `AGENTS.md` 第 15.11 节 |
| 31.8h / 48h | 新鲜度稳态滞后 / 最终阈值 | `docs/sprint/SPRINT_11.md` 第 7.6 节 |
| b5197e5121e08d689daebb72d3e626e2 | nginx 配置 live 与仓库副本的 md5 | `docs/sprint/SPRINT_11.md` 第 10.1 节 |
