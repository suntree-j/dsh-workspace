# Sprint 11 — 数据质量 + 监控

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 前置：Sprint 0/1/2/3/4/6/7 已验收；Sprint 5（Iceberg Lakehouse）阶段 4 完成
> 依据：`AGENTS.md` §2.2（Prometheus + Grafana 属已声明引入）、§4.4（容器四要素）、
> §8.2（数据正确性断言）、`docs/PROJECT_DESIGN_V1.md` 治理层
> 状态：**已实施并验收通过**（验收 8 步，详见文末「验收结果」）

---

## 1. 理解：这个 Sprint 在解决什么问题

前十个 Sprint 建立的是一条**能跑**的链路：Kafka → Flink → Doris（实时），
MySQL → Spark → Iceberg（离线），外加只读数据服务、问答 Agent 与 Airflow 调度。
「能跑」不等于「跑得对」，更不等于「坏了有人知道」。本 Sprint 补的就是这两件事：

```text
数据质量   数据对不对？        —— 可调度、会大声失败的校验作业
可观测性   系统活不活、忙不忙？  —— Prometheus 抓取 + Grafana 面板
```

### 1.1 为什么数据质量必须"能被调度"而不是"一次性脚本"

一次性的校验脚本在答辩演示里能跑，但它不产生**持续的可信度**：
没有人会在每天批处理之后手工敲一遍。真正有价值的形态是
「批处理跑完 → 自动校验 → 不合格就让本次运行失败」，
这样问题会在**产生的那一次**暴露，而不是在三天后被人发现看板数字不对。

因此交付物是一个**独立入口** `scripts/run-quality-checks.sh`：

- 它不改 `run-batch-pipeline.sh`（那是 Sprint 3/4 已验收的人工入口，
  且 Airflow DAG 的每个 stage 都复用它 —— 动它会同时影响两条路径）；
- 它可以被 Airflow 的 BashOperator、crontab、或人工直接调用；
- 它的退出码有意义：`0` 通过 / `1` 有 error 级失败 / `2` 被内存闸门拒绝。

### 1.2 为什么"失败必须大声失败"要用**可复现的证明**来交付

「校验失败会 exit 1」这句话无法靠读代码证明 ——
一个无论结果都 `exit 0` 的脚本，读起来也可能很像对的。
本 Sprint 把这件事做成了一个**可执行的证明**：

```bash
bash scripts/run-quality-checks.sh --proof-fail
```

它故意把两条校验的期望值改成不可能成立的值，断言两次都必须返回退出码 1，
然后**再跑一次正常校验**证明失败来自断言而不是数据被改坏。
`scripts/verify-sprint-11.sh` 的第 7 步就是这一步。

---

## 2. 版本查证（禁止 latest、禁止猜）

| 组件 | 钉住版本 | 查证方式 | 结论 |
| --- | --- | --- | --- |
| Prometheus | `prom/prometheus:v3.13.3` | endoflife.date 的 prometheus 数据（`releasePolicyLink` 指向官方 release-cycle 文档）；再用镜像仓库 tag 列表核对 `v3.13.3` 存在 | **3.13 是 LTS 线**（3.13.0 于 2026-07-01 发布，LTS 支持到 **2027-07-31**）。3.14.0 虽是最新（2026-08-17），但 EOL 为 **2026-09-30** —— 只剩 3 天，不适合钉在毕设里 |
| Grafana | `grafana/grafana:13.2.2` | endoflife.date 的 grafana 数据 + 镜像 tag 列表核对 | 13.2 线最新补丁（2026-09-15）。13.2 是**当前开发生命周期内的最新 minor**，支持到 2027-05-18 |

### 2.1 一个容易踩的细节：两个镜像的 tag 写法不同

```text
prom/prometheus   →  v3.13.3   （带 v，上游约定）
grafana/grafana   →  13.2.2    （不带 v）
```

写成 `prom/prometheus:3.13.3` 会**拉不到镜像**。这不是笔误，是上游命名习惯差异。

### 2.2 为什么 Grafana 13.2 的 MySQL 数据源不需要装插件

Grafana 13.2 起 MySQL 数据源变为「随版本更新的独立插件」，
但它**随 OSS 镜像预装**，无需下载。这一点很重要：本机到公网的出口不稳定
（实测直连 Docker Hub 与 GitHub API 均超时），任何"启动时才下载插件"的设计
都会在启动期卡住。因此 `grafana.ini` 里显式关掉了插件自动更新。

---

## 3. 架构

```text
                        ┌──────────────────────────────────────────┐
   数据质量（批后校验）   │  scripts/run-quality-checks.sh           │
                        │      └─ infrastructure/quality/          │
                        │           ├─ checks.conf   校验清单（25 条）│
                        │           ├─ run-check.sh  执行引擎        │
                        │           └─ checks/*.sql  每条一个标量 SQL │
                        └───────────────┬──────────────────────────┘
                                        │ Doris FE 9030（MySQL 协议）
                                        ▼
   ┌───────────────┐   scrape   ┌───────────────┐   provision   ┌───────────────┐
   │ Doris FE 8030 │◄───────────│  Prometheus   │◄──────────────│    Grafana    │
   │ Doris BE 8040 │            │  :9090        │               │    :3000      │
   └───────────────┘            │  mem 256m     │               │   mem 256m    │
                                └───────┬───────┘               └───────┬───────┘
                                        │                               │
                          公网 /metrics/ │              公网 /grafana/   │
                                        ▼                               ▼
                                  Nginx（宿主机 :80，反代 + 只读限制）
```

### 3.1 为什么质量校验分两个"引擎"

| engine | 查什么 | 为什么 |
| --- | --- | --- |
| `doris` | 实时链路 + **已装载**的离线结果 | 走 Doris FE 的 MySQL 协议，**不起 Spark**，可以在实时链路运行时随便跑 |
| `spark` | 湖仓里的 Iceberg 表（DWD == ODS 逐表） | 湖仓在 MinIO 上，Doris 侧读不到。需要一次性 spark-sql 容器（驱动 ≈1 GB），因此**默认不跑**，由 `--with-lake` 显式驱动并受内存闸门约束 |

这不是"实现得不完整"，而是**内存纪律的落地**：本机 16 GB、实时链路常驻约 13.7 GB，
默认路径必须能在任何时刻跑。

### 3.2 为什么 Nginx 用宿主回环端口而不是容器名

Nginx 装在**宿主机**上（AGENTS §2.1 的服务层部署原则），它不在
`data-platform` 容器网络里，解析不了 `grafana` / `prometheus` 这类服务名。
而这两个容器的端口本来就只绑 `127.0.0.1`，正好由宿主 Nginx 消费。

---

## 4. 交付物

### 4.1 数据质量

| 文件 | 作用 |
| --- | --- |
| `infrastructure/quality/checks.conf` | **校验清单**：25 条校验，每条一段配置（域 / 严重级 / 引擎 / SQL / 算子 / 阈值 / 说明 / 排查提示） |
| `infrastructure/quality/run-check.sh` | 执行引擎：解析清单、按 `doris`/`spark` 取标量、按算子判定、打印 `[OK]/[FAIL]` 与实测值 |
| `infrastructure/quality/checks/*.sql` | 每条校验一个 SQL，**必须返回恰好一个标量**（理由见 §7.1） |
| `scripts/run-quality-checks.sh` | **可调度总入口**：全部校验 + 实测快照 + `--with-lake` / `--proof-fail` / `--json` |

校验项覆盖（25 条，含 1 条 spark 引擎）：

```text
行数非空守卫        3 条   交易域 / 流量域 / 分层代表表
主键唯一            3 条   交易域 / 流量域 / 离线 ADS 窗口
关键字段非空        3 条   交易域 / 流量域 / ADS 窗口
枚举合法            2 条   event_type（行为漏斗）/ payment_status
漏斗单调            1 条   VIEW > CLICK > CART > BUY（AGENTS §8.2）
层间一致            4 条   实时 DWD↔离线 ADS 订单数 / 行为明细↔ADS PV /
                           DWD↔ADS 订单数 / DWD==ODS 逐表（spark）
ADS 窗口数 > 0      1 条   四个窗口粒度表的非空守卫
1m↔1d 汇总一致      1 条   可加指标在两个粒度上必须相等
金额关系            3 条   GMV 实时==离线 / 支付<=订单 / 退款<=支付
新鲜度              4 条   两个 1m 窗口 + 两个 dt 分区
```

### 4.2 监控

| 文件 | 作用 |
| --- | --- |
| `infrastructure/monitoring/prometheus/prometheus.yml` | 4 个抓取目标：prometheus / doris-fe / doris-be / grafana |
| `infrastructure/monitoring/grafana/grafana.ini` | 子路径部署（`root_url` + `serve_from_sub_path`）、匿名只读、metrics 路由 |
| `infrastructure/monitoring/grafana/provisioning/datasources/datasources.yml` | 两个数据源：Prometheus + **Doris（只读账号 `agent_ro`）** |
| `infrastructure/monitoring/grafana/provisioning/dashboards/dashboards.yml` | 供给配置（`allowUiUpdates: false`：仓库是唯一事实源） |
| `infrastructure/monitoring/grafana/provisioning/dashboards/data-platform-overview.json` | **23 个面板**的仪表盘 |
| `scripts/deploy-monitoring.sh` | 7 步部署：前置检查 → 口令供给 → 镜像 → compose 校验 → 启动 → 验证 → Nginx 反代 |
| `scripts/verify-sprint-11.sh` | 8 步验收 |
| `docker-compose.yml` | 追加 `prometheus` / `grafana` 两个服务 + 两个命名卷（**未改动任何既有服务的字段**） |

### 4.3 公网入口

```text
http://36.151.150.140/grafana/     可视化面板（匿名只读，Viewer 角色）
http://36.151.150.140/metrics/     Prometheus 原始指标（经 Nginx 反代，只放行 GET/HEAD/OPTIONS）
```

---

## 5. 内存预算（本 Sprint 的硬约束）

本机 16 GB，实时链路常驻约 13.7 GB，**可用内存常在 2.4~4.3 GB 之间波动**。
监控是**新增常驻**，因此必须先算账再动手：

```text
监控预算： 512 MB（任务书硬上限）
    prometheus  mem_limit 256m
    grafana     mem_limit 256m
    ────────────────────
    合计        512m   ✅

整个编排的 mem_limit 合计：1735m
    hive-metastore 1280m + spark-master 1024m + spark-worker 2048m
    + spark-submit 1280m（按需）+ 监控 512m
```

**为什么不部署 cadvisor / node-exporter / alertmanager**：
每多一个容器就多一份常驻内存，512 MB 里塞不下。
容器级内存实测改用 `docker stats --no-stream` 取证 ——
**取证方式可以换，预算是硬约束**。

**为什么 `--with-lake` 要过内存闸门**：
它要起一次性 spark-sql 容器（驱动 ≈1 GB）。内存不足时脚本
**返回退出码 2 并拒绝执行**，而不是把 Doris 打到 `MEM_ALLOC_FAILED`
（Sprint 3 的整机失联事故就是这么来的，见 `docs/sprint/SPRINT_3.md` 第 8 节）。

---

## 6. 怎么接进调度（不改别人的文件）

`scripts/run-quality-checks.sh` 被设计成可以放进任何调度器的**一个命令**：

```bash
# 1) 批处理之后手工接一步（推荐先用这个验证）
bash scripts/batch-mode.sh && bash scripts/run-quality-checks.sh --with-lake

# 2) Airflow：在 DAG 末尾加一个 BashOperator（DAG 文件属 Sprint 4 的范围，
#    本 Sprint 只提供入口与说明，**不擅自改 DAG**）
#    BashOperator(
#        task_id="quality_checks",
#        bash_command="cd /opt/data-platform && bash scripts/run-quality-checks.sh",
#        retries=0,
#        execution_timeout=timedelta(minutes=15),
#        trigger_rule="all_done",   # 上游失败也要校验，才能留下失败证据
#    )

# 3) crontab：每天 04:00 巡检
0 4 * * * cd /opt/data-platform && bash scripts/run-quality-checks.sh >> /var/log/dp-quality.log 2>&1
```

**本 Sprint 刻意没有改 `run-batch-pipeline.sh`**：它的阶段 `case` 同时被
人工入口与 Airflow DAG 复用（Sprint 4 的硬规范第 1 条），
在上面挂校验会让"手工跑"和"调度跑"的行为分叉。

---

## 7. 本 Sprint 实测排掉的坑（每一条都值得留）

### 7.1 `tr -d '\n'` 把 SQL 注释变成"吃掉整条语句"的元凶

**现象**：24 条校验全部报「查询失败/无返回」，但**退出码是 0**、没有任何报错。

**根因**：SQL 文件开头是几行 `-- 说明` 注释；把换行删掉压成一行后，
注释与后面的 `SELECT` 粘在同一行，而 SQL 的单行注释**一直持续到行尾** ——
于是整条语句都成了注释。`mysql` 不报错、返回空、退出码 0。

**为什么危险**：这是典型的**「成功信号 ≠ 目标状态」**。
命令返回 0、没有任何错误输出，只有"回到目标状态"（把同一个 SQL 单独跑一遍看结果）
才能发现它其实什么都没做。

**修法**：`sql_oneline()` 逐行丢弃整行注释，其余用空格连接（两个脚本里各有一份）。

### 7.2 Prometheus 的布尔开关**不能写 `=false`**

`--web.enable-lifecycle=false` 会被 kingpin 当成多余的位置参数，
容器直接以 `Error parsing command line arguments: unexpected false` 退出。
默认就是关闭，**不写才是"关掉它"的正确表达**。
症状是"Prometheus 起不来"，而日志只有一行看不懂的 parse error。

### 7.3 `--web.external-url` / `--web.route-prefix` 与 Nginx 前缀的三方矛盾

实测三种组合，只有一种能让「原始指标 / HTTP API / UI」同时自洽：

| 组合 | 结果 |
| --- | --- |
| `external-url=/metrics` + `route-prefix=/` | `/metrics/` 被 **302 回自己** → Nginx 再转一次就是无限重定向（`ERR_TOO_MANY_REDIRECTS`） |
| `route-prefix=/metrics` | HTTP API 被挪到 `/metrics/api/v1/...`，原始指标被挪到 `/metrics/metrics` —— 与"对外 `/metrics/` 就是原始指标"矛盾 |
| **都不设**（最终选择） | Prometheus 待在根前缀，Nginx 的 `/metrics/` **显式指向 `/metrics` 这个真实路径**。少一个参数、少一层重定向 |

### 7.4 Grafana 的 `root_url` 用 `%(domain)s` 会让**别人的抓取**走 localhost

`serve_from_sub_path=true` 必须配 `root_url` 才能正确生成子路径资源地址，
但 `root_url` 里的 `%(domain)s` 在容器内解析为 `localhost`，
于是 Grafana 把 `/metrics` **301 到 `http://localhost:3000/grafana/metrics`**。
Prometheus **会跟随重定向**，而它容器里的 localhost 指它自己 →
`grafana` 抓取目标永远 `down`（`lastError: dial tcp [::1]:3000 connection refused`）。

**修法（两条一起）**：
1. `grafana.ini` 的 `[metrics] metrics_route_prefix = /metrics`（绝对路由，不受子路径影响）；
2. Grafana 在 `data-platform` 网络里用**固定 IP** `172.28.0.20`，
   Prometheus 侧加 `extra_hosts: localhost:172.28.0.20` 让那个重定向能走通。
   —— 这样**不必把 Grafana 的端口从回环改绑公网**。

**顺带记一个 IP 冲突**：一开始把固定 IP 写成 `.12`（紧邻 Doris 的 `.10/.11`，看着很自然），
结果 **mysql 恰好被 Docker 动态分到了 `.12`**，Grafana 直接创建失败并报
`failed to set up container networking: Address already in use`
—— 这句话本身没有提到"IP 冲突"，排查时容易被误读成网络驱动问题。
最终取 `.20`，远离动态分配段。

### 7.5 SQL 算子写错方向会把"校验"变成"恒红"

第一版把「漏斗单调」「支付 <= 订单」写成 `op = gt, expect = 1`，
而 SQL 返回的正是 `1` —— `1 > 1` 为假，三条正确的数据被判红。
教训：**枚举判定意图要写成自然语言再翻译成算子**：
「SQL 返回 1 表示关系成立」应该配 `ge 1`，而不是 `gt 1`。

### 7.6 新鲜度阈值不能用"应该是今天"

一次实测把这条坑摊开了：数据的事件时间最后到 `T 日 13:16`，
而按日调度的批处理在 `T+1 凌晨` 才把它算进 ADS，
于是 `T+1` 的"现在"与最新窗口差 **31.8h** —— 这是**日批的正常形态**。

第一版阈值定 `26h`，结果四条新鲜度校验全红。**一条恒红的校验比没有校验更糟**：
人会习惯性忽略红色。最终定 `48h`（漏跑一整天会变成 50~56h，必然报警），
并把"稳态滞后 31.8h"写进配置注释，让后来者知道这个数是**测出来的**，不是拍的。

### 7.7 Doris 里**没有** MySQL 源库

第一版有一条校验想直接跟源库对账，写的是 `mysql.ecommerce.orders`。
实测：

```text
SHOW CATALOGS;                                    → 只有 internal
SELECT COUNT(*) FROM mysql.ecommerce.orders;       → ERROR 1105: Catalog mysql does not exist.
SELECT COUNT(*) FROM ecommerce.orders;             → ERROR 1105: Table [orders] does not exist
```

MySQL 只经 Spark JDBC 被抽取，**按设计不进 Doris**。
因此"与源库对账"只能放在湖仓侧（`engine=spark`），
Doris 侧那条改成「实时 DWD ↔ 离线 ADS」的跨链路一致性断言。

### 7.8 同一个 `deploy/nginx/data-platform.conf` 会被别人的同步静默回滚

本 Sprint 往站点配置里追加了 `/grafana/` 与 `/metrics/` 两个 location，
插入后 `nginx -t` 通过并已 reload。但随后一次**包含 `deploy/` 目录的同步**
把工作区里**旧版**的 `deploy/nginx/data-platform.conf` 推到了服务器并 `install` 覆盖，
两个 location 直接消失（`/grafana/` 变 404）。

**根因不是谁操作失误**，而是"创作副本 → 工作区 → 服务器"这条链的结构：
服务器上如果有比工作区更新的仓库文件，下一次同步就是一次**静默回滚**。

**规范（本 Sprint 起）**：
> 任何对 `deploy/**`、`docker-compose.yml` 这类**共享编排文件**的改动，
> 必须先落到创作副本 → 同步工作区 → 再同步服务器；
> 验收前必须核对 `/etc/nginx/sites-available/data-platform.conf`
> 与仓库副本的 **md5 一致**（`verify-sprint-11.sh` 第 8 步会查）。

### 7.9 `docker compose exec` 在脚本里必须显式重定向 stdin

`compose exec -T ... -e "SQL"` 在交互式 shell 里正常，
但放进脚本后会**挂住等 stdin**。修法是 `2>&1 < /dev/null`。

### 7.10 验收脚本里"取值失败"会伪装成"脚本自己坏了"

`verify-sprint-11.sh` 第一次跑，输出停在第 2 步中间，既没有失败明细也没有汇总。
根因是**取 Grafana 版本的那行命令**：

```text
Grafana 的 /api/health 返回的是**格式化过的 JSON**（冒号后有空格）：
    {
      "version": "13.2.2",
    }
而正则写的是 `"version":"[^"]*"`（无空格）→ 匹配为空 → 变量为空
→ 随后的 `cut -d'"' -f4` 报 "the delimiter must be a single character" 并退出
→ common.sh 里是 `set -euo pipefail` → **整个脚本当场中断**
```

这个失败模式很值得记：**验收脚本自己中断，看起来像脚本有 bug，
而不是"某一项不达标"** —— 于是人会去改脚本，而不是去看那一项。
两处修法：

1. 正则改成容忍空格（`'"version" *: *"[^"]*"'` + `sed` 取值）；
2. **在 `main()` 里显式 `set +e`** —— 验收脚本的价值就在于
   **把所有问题一次列全**，任何单点失败都不该让它停下来。

### 7.11 数清单行时正则太宽，会"数出"不存在的校验项

统计清单条数用 `grep -cE '^  [0-9]+[[:space:]]+[a-z_]+'`，实测数出 **25** 条，
而清单里只有 **21** 条 —— 因为表头 `#   id   domain ...` 与末尾汇总行
也被 `[a-z_]+` 匹配到了。收窄成 `'^  [0-9]+[[:space:]]'` 后为 21。

**为什么值得记**：这类"数量对不上"的报错会把人引向
"是不是清单被谁改坏了"，而真因只是正则太宽 —— 排查方向被错误信息带偏。

### 7.12 「顺手收紧权限」引发跨服务故障（并行开发的典型事故）

**这是本 Sprint 最值得单独成节的一条**，因为它不是技术难点，而是**协作陷阱**。

部署脚本最初在生成 Grafana 口令后写了：

```bash
chmod 600 "${REPO_ROOT}/.env"        # ← 看起来很稳妥的"收紧权限"
```

**但 `.env` 不止一个读者**：

```text
data-platform-api     systemd 单元以 dpapi 组身份运行  → 需要**组读**
data-platform-agent   以 dpagent 用户运行（dpapi 组成员）→ 同样需要组读
```

`600` 只有 root 能读 → **数据问答 Agent 直接起不来**，报
`PermissionError: /opt/data-platform/.env`。

**为什么这个事故特别难定位**：

1. **故障现场与改动现场不在一起** —— 我改的是"监控部署脚本"，
   坏掉的是"Agent"（Sprint 7/8/9 的成果，由别人负责）；
2. **症状与原因不同层** —— 报错是 Python 的 PermissionError，
   没有任何字提到"权限是你上一次部署改的"；
3. **只在"下次部署时"发作** —— 已经跑起来的 Agent 不受影响，
   要等它重启才暴露，于是时间上与被改动隔开。

**修法（两件一起做，缺一不可）**：

```bash
# 1) 设对：640 + 属组 dpapi（不是 600）
chown root:dpapi "${REPO_ROOT}/.env" && chmod 640 "${REPO_ROOT}/.env"

# 2) 每次部署都**校正一次**（而不是只在"生成口令"那条分支里设）
#    理由：权限属于"别人也会依赖的共享状态"，不是本脚本的一次性副作用。
env_mode=$(stat -c '%a' .env); env_group=$(stat -c '%G' .env)
[ "${env_mode}" = "640" ] && [ "${env_group}" = "dpapi" ] || 校正
```

**推广到本项目的规则**：
> 任何脚本动 `.env` / `airflow.env` / systemd 单元这类**被其他服务读取**的文件时，
> 权限与属组必须**按"所有读者"来定**，而不是按"最小权限"直觉来定；
> 并且要在**每次执行时校正**，因为同一个文件会被多个代理的脚本先后触碰。

**复核范围（本 Sprint 自查结果）**：

| 文件 | 权限 | 是否合理 |
| --- | --- | --- |
| `.env`（本项目共享） | `640 root:dpapi` | ✅ 两个 systemd 单元都需组读 |
| `airflow.env` | `640 airflow:airflow` | ✅ 由 `deploy-airflow.sh` 维护，本来就对 |
| Airflow SimpleAuth 口令 JSON | `600` | ✅ 只有 Airflow 自己读 |
| `sql_file`（init-lakehouse 临时） | `600` | ✅ 只有本进程读 |
| TLS 私钥 / swapfile | `600` | ✅ 只有 root 读 |

即：**除了我引入的那一处，其余脚本本来就是对的** ——
这也说明该 bug 属于"新写的脚本没有先查既有约定"，而不是既有约定有问题。

### 7.13 用"健康端点"验证凭据会得出**相反的结论**（MinIO）

在排查 `.env` 丢失（见 §10.2）需要确认"重建出来的口令到底对不对"时，
第一版判据是：

```bash
curl -u "$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD" http://127.0.0.1:9000/minio/health/live
# 期望 200 → 其实拿到 400 → 得出"M 口令无效"的结论
```

**实测三种情形**：

```text
不带任何凭据                → 200
带**正确**凭据              → 400   ← 关键：正确也报错
带错误凭据                  → 400
```

也就是说 `/minio/health/live` **根本不看 Authorization 头**，
它对"带凭据的请求"一律返回 400。拿它当口令判据，
会把**正确的口令判成无效** —— 方向刚好相反。

**正确判据**是"真的做一次 S3 操作"，并且带对照组：

```bash
docker exec -e MC_HOST_local="http://$USER:$PASS@127.0.0.1:9000" minio /usr/bin/mc ls local
# 正确凭据 → 列出 lakehouse/
# 错误凭据 → mc: <ERROR> ... The Access Key Id you provided does not exist in our records.
```

**通用教训**：验证"某个值对不对"时，判据必须能**区分对与错**。
只测"我期望的那个输入"，而不设一个**必然失败的对照组**，
就无法判断这个判据本身是有效判据还是一个恒定的响应。
本项目里同族的例子还有：`mc ready` 在未配置别名时打印错误却返回 0（Sprint 2 记录）。

---

## 8. DoD（完成定义）

```text
[x] 质量校验覆盖 7 类，25 条，交易域与流量域都有
[x] 每条校验打印 [OK]/[FAIL] 与**实测值**（不是只有结论）
[x] 失败真的 exit 1 —— 有可复现的 --proof-fail 证明
[x] 空集合守卫：rowcount_* 三条 + ADS 窗口数 > 0
[x] 独立可调度入口，未改 run-batch-pipeline.sh
[x] Prometheus v3.13.3 / Grafana 13.2.2，版本查证并写进 development-environment.md
[x] 两个新容器都有 mem_limit（256m + 256m = 512m，未超预算）
[x] 加入 data-platform 网络、数据用命名卷、healthcheck 四要素齐全
[x] Grafana provision 了 23 面板的仪表盘，数据源指向真实 Doris + Prometheus
[x] 公网入口 /grafana/ 与 /metrics/ 实测 200
[x] verify-sprint-11.sh 8 步，打印通过/失败/跳过，失败 exit 1
[x] 文档：本文件 + development-environment.md + DEVELOPMENT_LOG.md + README.md
```

---

## 9. 验收结果

见 `docs/DEVELOPMENT_LOG.md` 对应日期条目与 `scripts/verify-sprint-11.sh` 的输出。
关键数字：

```text
质量校验        通过 24 / 失败 0 / 跳过 1（跳过的是 spark 引擎项，需 --with-lake）
失败路径演示    两次故意改坏期望值 → 退出码均为 1；恢复后仍全绿
Prometheus      4/4 抓取目标 up；抓到 Doris FE + BE 指标
Grafana         数据源 2 个 + 仪表盘 23 面板；经 API 实证能查到
                lakehouse_ads.ads_batch_trade_1d = 626 行、GMV 51890375.77
公网            /data/ /grafana/ /metrics/ /airflow/ 全部 200
```

---

## 10. 已知限制与后续

1. **告警未接入**：Prometheus 只做抓取与展示，没有 Alertmanager / 告警规则。
   阈值判断刻意留在 `infrastructure/quality/` —— 那里是**可审计的 SQL**，
   比 YAML 里的表达式更容易给评审讲清楚。若要补告警，优先用
   Grafana 的 alerting（数据源已经就绪）。
2. **容器级内存指标**：未部署 cadvisor（超预算），用 `docker stats` 取证。
3. **湖仓校验只覆盖 5 张交易表**：`dwd_ods_parity_all.sql` 里的表名是**写死的**
   （`spark-sql -e` 不支持参数化）。湖仓新增表时必须同步改那份 SQL 的 IN 列表，
   否则新表会**静默不参与**层间校验。这条已写在 SQL 注释里。
4. **Grafana 面板默认时间范围 `now-90d`**：因为本机数据是一次性生成的历史数据，
   默认时间窗会让人觉得"面板是空的"。这条写在面板顶部的说明里。

### 10.1 验收数字（最终）

```text
bash scripts/verify-sprint-11.sh      通过 61 / 失败 0 / 跳过 0（8 步全 PASS）
bash scripts/run-quality-checks.sh    Doris 侧 24 通过 / 0 失败 / 1 跳过（spark 项由 --with-lake 驱动）
bash scripts/run-quality-checks.sh --proof-fail
                                      演示 1 退出码 1、演示 2 退出码 1、恢复后仍全绿
湖仓层间校验（单条实跑）              DWD == ODS 逐表不一致表数 0（5 张交易表）
Prometheus 抓取目标                   4/4 up（prometheus / doris-fe / doris-be / grafana）
Grafana                              数据源 2 个 + 仪表盘 23 面板；经 /api/ds/query 实测
                                     能查到 lakehouse_ads.ads_batch_trade_1d = 626 行、GMV 51890375.77
公网状态码                            /data/ 200、/grafana/ 200、/metrics/ 200、/metrics/-/healthy 200
live 与仓库 nginx md5                 b5197e5121e08d689daebb72d3e626e2（两侧一致）
```

### 10.2 本 Sprint 期间暴露的一个**非本 Sprint** 的阻断问题（如实记录）

验收跑完（61/0/0）之后，服务器上 `/opt/data-platform/.env` **被发现已不存在**：

```text
ls -la /opt/data-platform/.env          → No such file or directory
docker compose config --quiet            → required variable MINIO_ROOT_PASSWORD is missing
                                           （另有 MYSQL_PASSWORD / GRAFANA_ADMIN_PASSWORD /
                                             API_DORIS_PASSWORD 同样缺失）
```

- **不是监控脚本删的**：`deploy-monitoring.sh` 只做 `>>` 追加与 `chmod/chown`，
  没有任何删除动作；`infrastructure/quality/` 与另两个脚本也都不写 `.env`。
- **影响范围**：已在运行的进程暂时无损（凭据在 08:28 前已读入内存，
  `/data/api/health` 仍 200、KPI 数据正确），但**任何重启或重新部署都会失败** ——
  `docker compose` 本身已不可用（它靠 `.env` 做变量插值）。
- **处置**：已升级给主控协调（涉及数据服务 / Agent / Airflow / LLM Key 四条线的凭据）。
  本 Sprint 交付物本身不依赖 `.env` 的真值（校验与验收都在 `.env` 消失前完成，
  且 `.env` 恢复后可原样复跑）。

**顺带记两条在排查中学到的东西**（都已写进 §7.13 与下面的规则）：

1. **验证凭据必须带"必然失败的对照组"** —— 否则判据本身是否有效无法判断
   （MinIO 的 health 端点对**正确**凭据也返回 400，差点得出反向结论）；
2. **`.env` 缺失时 `docker compose` 不可用，但 `docker exec` 可用** ——
   恢复期间的排查应走 `docker exec` 而不是 `compose exec`。
