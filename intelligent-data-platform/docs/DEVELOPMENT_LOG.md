# 开发日志（Development Log）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 用途：按时间顺序记录**每个阶段做了什么、验证了什么、遇到什么问题、做了什么决策**。
> 维护要求：每完成一个阶段（代码 / 测试 / 验证 / 文档）就追加一条，见 `AGENTS.md` 第 14 节。

---

## 目录

- [2026-09-26 Sprint 0](#2026-09-26-sprint-0)
- [2026-09-26 Sprint 1](#2026-09-26-sprint-1)
- [2026-09-26 Sprint 6（服务层，顺序前移）](#2026-09-26-sprint-6服务层顺序前移)
- [运行环境](#运行环境)
- [待办与下一步](#待办与下一步)

---

## 运行环境

| 项目 | 值 |
| --- | --- |
| 开发机 | Windows 11（`C:\Users\jsy28\Desktop\dsh-workspace`） |
| 运行环境 | 腾讯云轻量服务器 `36.151.150.140`（Ubuntu 24.04.2 LTS，4C/16G/100G） |
| 应用目录 | `/opt/data-platform` |
| 登录 | `ssh -i ~/.ssh/suntree.pem root@36.151.150.140` |
| 仓库 | https://github.com/suntree-j/dsh-workspace |
| 容器 | Docker 29.8.1 / Compose v5.5.1 / containerd 2.3.5 |

---

## 2026-09-26 Sprint 0

**主题**：项目初始化与基础数据环境
**状态**：✅ 已完成并在服务器上通过全部验收

### 阶段 1：仓库与环境审计

- 发现工作区为空且未初始化 Git；本机**没有任何容器运行时**
  （无 Docker Desktop、无 Podman、无 WSL 发行版）
- 决策：容器验证放到腾讯云服务器执行，本地只做开发
- 建立 `intelligent-data-platform/` 项目骨架与 `.gitattributes`
  （强制 `*.sh` 为 LF —— 本地 `core.autocrlf=true` 会把 Shell 脚本
  检成 CRLF，导致 Linux 下报 `bash\r: No such file or directory`）

### 阶段 2：镜像版本查证（不猜版本）

逐个查证官方仓库后确定：

| 组件 | 版本 | 关键决策依据 |
| --- | --- | --- |
| MySQL | `8.4.11` | `mysql/mysql-server` 2023 年起停更，改用官方 `mysql` 库镜像的 LTS 线 |
| Kafka | `4.2.1` | 官方 `apache/kafka` 镜像；4.x 起移除 ZooKeeper，默认 KRaft |
| MinIO | `coollabsio/minio:RELEASE.2025-10-15T17-29-55Z` | **MinIO 自 2025-10 停止免费分发镜像**，官方组织已从 Docker Hub 下架（404），该 tag 为停发前最后一个官方发行版 |
| Doris | `apache/doris:fe-4.1.4` / `be-4.1.4` | FE/BE 必须同版本；`ms-` 镜像仅存算分离需要 |

记录多架构 manifest digest 以便复现。

### 阶段 3：编排与初始化脚本

- 统一网络 `data-platform`（`172.28.0.0/24`）
- **关键约束（从镜像内脚本实测得出）**：Doris 的 `init_fe.sh` 用正则
  校验 `FE_SERVERS=<name>:<IPv4>:<port>`，**拒绝主机名**，且会把
  `priority_networks` 推导为 `<IP 前三段>.0/24`
  → 因此 FE/BE 必须用固定 IP `172.28.0.10` / `172.28.0.11`
- Kafka 采用双监听器：容器 `kafka:9092` / 宿主机 `localhost:19092`
  （`advertised.listeners` 必须是客户端能直连的地址，容器与宿主机不同）

### 阶段 4：数据生成器

- 两个正式入口：`python -m src.generate_mysql_data` / `python -m src.generate_events`
- 通过 `state/dataset_snapshot.json` 让 Kafka 事件与 MySQL 真实数据一致
  （避免「Kafka 里的 order_id 在 MySQL 中不存在」）
- 24 个单元测试覆盖外键关系、金额逻辑、时间因果、漏斗单调性
- 修复 bug：漏斗权重未归一化（VIEW 权重 1.0 独占全部配额，总量翻倍）
  与 `ladder_min` 取反（VIEW 拿到 0 导致降级循环把 CLICK/CART 清零）

### 阶段 5：服务器部署

服务器位于中国大陆网络环境，遇到两个硬性障碍：

| 障碍 | 处理 |
| --- | --- |
| `get.docker.com` 被重置 | 改用清华镜像的 `docker-ce` apt 源 + 多源回退获取 GPG 公钥 |
| `registry-1.docker.io` / `auth.docker.io` 超时 | 配置国内 registry mirror；Doris 大镜像用 **SSH 反向隧道共享本地代理**（FE 1.6 GB 从 30 分钟降到 12 秒） |

同时：

- `vm.max_map_count` 1048576 → **2000000**（Doris 要求），持久化到 `/etc/sysctl.d/`
- 修复本地 `~/.ssh/config` 的 UTF-8 BOM（导致 Git Bash 的 ssh 报
  `Bad configuration option`；Windows 自带 ssh 容忍 BOM 所以此前未暴露）

### 阶段 6：验收与修复（本 Sprint 的主要工作量）

依次修复 9 个缺陷，**全部是只有真正跑起来才会暴露的问题**：

| # | 问题 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | Doris BE 每 ~15 秒重启 | 上游 `be-4.1.4` 的 `entry_point.sh` 在 `check_be_status` 成功后即返回，容器 PID 1 退出被 Docker 重启 | 新增 `be-keepalive.sh` 保活包装 |
| 2 | **Doris 拒绝在有 swap 时启动** | `start_be.sh` 输出 `Disable swap memory before starting be`；我按常规建议加的 8G swap 直接导致 BE 起不来，进而 tablet 变坏副本 | 移除 swap，并在 sysctl 配置中显式记录「本机必须保持 swap 关闭」 |
| 3 | 初始化 SQL 从未执行 | `process_init_files` 位于 `check_be_status` 之后的代码路径，容器在之前就退出了 → `ecommerce` 库与 `test_connection` 表实际不存在 | 保活包装在 BE 就绪后主动补执行初始化目录（含重试） |
| 4 | 保活包装空转 | 循环调用 `start_be.sh --console`，BE 已运行时该脚本立即返回 rc=1 | 改用 Doris 自带 `--daemon`（本身即「启动+守护」） |
| 5 | Doris 初始化 SQL 中断 | `ALTER SYSTEM SET default_replication_num` 在 4.1.4 **不存在该系统变量**，直接中断整个脚本 | 删除，改为每表显式 `PROPERTIES("replication_num"="1")` |
| 6 | MinIO bucket 从未创建 | 镜像基于 BusyBox，**无 sed/awk/grep/curl/tar**；脚本用 sed 导致凭据变量为空 | 改 POSIX 参数展开 + `mc ls` 探测 |
| 7 | MinIO healthcheck 永远失败 | 用了镜像内不存在的 `curl`；且 `mc ready` 失败时**退出码竟为 0** | 新增 `healthcheck.sh`，用 mc 显式构造别名 |
| 8 | 数据生成器写快照权限拒绝 | 非 root 用户 + 宿主机属主挂载 `/app` | 快照目录改用命名卷 |
| 9 | `USE \`ecommerce\`` 报错 | 部分客户端下反引号导致 `USE must be followed by a database name` | 改用不带反引号的写法 |

另有 5 处**冒烟测试自身**的缺陷（漏 fixture 参数、误统计 Kafka 分区数、
用不存在的 curl、断言不存在的变量等）一并修正。

### 阶段 7：Sprint 0 验收结果

```text
✅ docker compose config        通过
✅ docker compose up -d         5 个核心服务全部 healthy
✅ scripts/health-check.sh      5/5 [OK]，exit 0
✅ python -m pytest             51 passed（24 单元 + 27 冒烟）
✅ 数据生成                     MySQL 1200/600/6000/5406/254
                                Kafka 31,660 条事件（漏斗逐级收窄）
✅ 数据持久化                   down / up 后数据 100% 保留
✅ 容器稳定性                   5 个容器 RestartCount 全部为 0
```

### 阶段 8：服务器收尾

- 清理排查期间遗留的 Doris backend 记录（官方要求用 `DROPP` 而非 `DROP`）
- 重置 Doris 存储卷，消除崩溃循环期留下的损坏 tablet（`isBad=true`）
- 验证初始化幂等：重复执行 seed 脚本不产生重复行（仍为 1 行）
- 移除 docker 的反向隧道代理配置，改为只依赖 registry mirror
  （避免隧道断开后镜像拉取全部失败）

### 决策记录（Architecture Decision Records 简版）

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 容器验证放哪 | 腾讯云服务器 | 本地无容器运行时且 Docker Desktop 需重启；服务器 4C/16G 更接近真实部署 |
| 是否加 SWAP | **不加** | Doris 明确拒绝在启用 swap 时启动；内存控制改用 Doris 自身配置 |
| Doris BE entrypoint | 覆盖为保活包装 | 上游镜像缺陷；已完整记录，上游修复后可移除 |
| Kafka 监听器 | 双监听器 | 单监听器无法同时满足容器与宿主机客户端 |
| MinIO 镜像 | 社区再托管 | 官方已停发；已固定 digest 并记录替代方案 |
| 事件与业务库一致性 | 快照文件 | 保证 Kafka 事件必然对得上 MySQL 记录 |

---

## 2026-09-26 Sprint 1

**主题**：Kafka + Flink + Doris 实时数仓
**状态**：✅ 已完成并在服务器上通过全部验收
**设计文档**：[`docs/sprint/SPRINT_1.md`](sprint/SPRINT_1.md)（含实现偏差记录）

### 阶段 1：设计先行（口径与分层）

先写文档再写代码，本 Sprint 只做一件事：**把一条实时链路跑通**。

- `docs/sprint/SPRINT_1.md`：分层（DWD/DWS/ADS）、指标口径、验证方案、完成标准
- `sql/metadata/metrics.md`：**指标口径字典**（唯一权威）
  —— 同一个「GMV」在全项目只能有一个定义，实时与离线（Sprint 3）必须同口径
- 明确**不引入** Spark / Hive / HDFS / Airflow / Iceberg / Agent 等后续 Sprint 组件

### 阶段 2：Doris 数仓表与 Routine Load

| 文件 | 内容 |
| --- | --- |
| `sql/doris/10_dwd_tables.sql` | 4 张 DWD 明细表 + 2 张维表（`dim_product` / `dim_user`） |
| `sql/doris/11_dws_tables.sql` | 1 张 DWS 汇总表（流量总览） |
| `sql/doris/12_ads_tables.sql` | 3 张 ADS 指标宽表（交易 / 流量 / 类目） |
| `sql/doris/13_routine_load.sh` | 8 个 Routine Load 作业（幂等，可重复执行） |

关键设计：所有明细/指标表统一 **UNIQUE KEY + merge-on-write**，
以业务主键或窗口为 key —— 事件重放、重复投递、窗口更新都自动收敛，天然幂等。

### 阶段 3：Flink 实时作业

```text
Kafka 事件 topic (4)
   └─ Flink SQL：清洗 / 补维 / 字段裁剪 ──► Kafka dwd_* topic (4) ──► Doris DWD (4)
   └─ Flink SQL：1 分钟滚动窗口聚合      ──► Kafka dws_*/ads_* topic (4) ──► Doris DWS/ADS (4)
```

**为什么不让 Flink 直写 Doris**：`doris-flink-connector` 需要 Doris / Flink /
connector 三方版本严格匹配，升级任一组件都要重新验证；而 `Routine Load` 是
Doris **内置**的 Kafka 导入能力，零额外依赖、天然断点续传与幂等。
代价是多一层 Kafka topic，可以接受。

### 阶段 4：踩坑与修复（本 Sprint 的主要工作量）

| # | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | connector JAR 下载 404 | 记录的 `3.2.0-1.20` 版本**不存在**（凭记忆写错） | 查 Maven Central 发布列表，改为 `3.4.0-1.20` |
| 2 | Flink 解析事件报 `Fail to deserialize at field: event_time` | 生成器输出 ISO-8601 带时区（`2025-07-16T13:42:44+08:00`），Flink 的 JSON 格式要求 `yyyy-MM-dd HH:mm:ss[.SSS]` | 生成器改为 `%Y-%m-%d %H:%M:%S.000`；并给行为事件补时间上界，避免生成未来时间 |
| 3 | 窗口作业编译失败：`Table sink ... doesn't support consuming update changes` | 窗口聚合产出的是 **update 流**（含撤回），普通 `kafka` sink 只支持 append | DWS/ADS sink 全部改为 `upsert-kafka` + `PRIMARY KEY` |
| 4 | upsert-kafka 报「不认识的选项」 | `json.encode.decimal-as-plain-number` 在 upsert-kafka 上要加 `value.` 前缀 | 改为 `value.json.encode.decimal-as-plain-number` |
| 5 | 作业提交报 `NoResourceAvailableException` | 8 个作业 × `parallelism.default=3` 远超 8 个 slot | source SQL 顶部 `SET 'parallelism.default' = '1'` |
| 6 | 类目作业编译失败：`Column types of query result and sink ... do not match` | Flink 写入**按位置对齐**；Doris 要求 UNIQUE KEY 为有序前缀，因此 sink 列顺序是 `(window_start, category_name, window_end, ...)`，而 SELECT 写成了 `(window_start, window_end, category_name, ...)` | 调整 SELECT 列顺序与 sink 一致，并在注释里写明原因 |
| 7 | **重启 `flink-jobs` 后指标翻倍**（PV 合计 39987，事件总数 20000） | SQL Gateway 是 **session 模式**：作业生命周期绑定在 session 上，**重启容器不会取消作业**。旧作业既占 slot（新作业拿不到资源而失败），又把重放的事件累加到旧窗口状态上 | 提交脚本启动时先取消所有非终态作业；新增 `scripts/cancel-flink-jobs.sh`；健康检查增加「作业唯一性」检查项 |
| 8 | 健康检查误报「8 个 Routine Load 全部缺失」 | mysql 客户端 `-N` 会去掉 `Name:` / `State:` 字段标签，解析必然失败（作业其实是 RUNNING） | 解析 `SHOW ROUTINE LOAD\G` 时**不加 `-N`**；并补上 `USE ecommerce;`（否则报 `No database selected`） |
| 9 | 某窗口 `gmv=23616` 但 `payment_cnt=NULL` | 支付发生时间与下单时间天然错位；NULL 会被下游 `SUM()` 忽略、被看板显示成空白，容易被误读为丢数据 | 明确口径：**可加指标补 0、比率指标留 NULL**，写入 `metrics.md` 第 2.1 节 |
| 10 | Doris Routine Load 全部 PAUSED、`errorRows == 总行数` | 报错只有 `ErrorLogUrls` 里才看得到：`JSON data is not an array-object, 'strip_outer_array' must be FALSE` | Routine Load 增加 `"strip_outer_array" = "false"` |
| 11 | DWD 行数比预期多一倍（12000 而不是 6000） | 早期失败的作业留下消费者位点 + 作业重复提交，同一批事件被消费两次 | 重建 topic 后重新生成事件；Doris 侧靠 UNIQUE KEY 保证最终幂等 |
| 12 | Routine Load 把「墓碑消息」当成错误行？ | upsert-kafka 在撤回 key 时会写 value=null 的墓碑消息（实测 29475 条消息中有 25 条） | 实测 Doris 会**跳过**这些消息（`loadedRows` 29450 = 29475 − 25，`errorRows` 0），无需处理 |

### 阶段 5：验收结果

```text
✅ 12 个 Kafka topic           4 个源 topic + 8 个下游 topic 全部就绪
✅ Flink 集群                  8 个 sink 作业各 1 个实例，slots 8
✅ Routine Load                8/8 RUNNING，errorRows 全部为 0
✅ DWD 落库                    订单 6000 / 支付 5406 / 退款 254 / 行为 20000
                               （与 MySQL 事实表、Kafka topic 偏移量三者一致）
✅ ADS 交易指标                SUM(order_cnt)=6000  SUM(gmv)=51890375.77
                               SUM(payment_cnt)=5287  SUM(refund_cnt)=254
                               —— 与 MySQL **精确相等（到分）**
✅ ADS 流量/类目指标           PV 合计 = behavior_event 事件数；类目 GMV 合计 = MySQL GMV
✅ 健康检查                    10 项全部 [OK]（Sprint 0 五项 + Sprint 1 五项）
✅ 冒烟测试                    tests/smoke 全部通过（含 47 个实时链路用例）
```

对账能做到**精确相等**而不是「允许误差」，是因为窗口按事件时间切分且不重叠：
逐窗口可加指标求和必然回到全量。任何一次对账不相等都意味着真的丢数或重复计算。

### 决策记录

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| Flink 结果如何进 Doris | 写回 Kafka + Doris Routine Load | 避免 doris-flink-connector 的三方版本绑定；Routine Load 是 Doris 内置能力且自带幂等与断点续传 |
| sink 连接器类型 | `upsert-kafka` + PRIMARY KEY | 窗口聚合产出 update 流，普通 kafka sink 不支持 |
| 类目维度怎么来 | 事件冗余字段，不做 lookup join | 避免 Sprint 1 引入外部系统依赖与启动时序问题；冗余字段表达的是"事件发生时点"的维度，语义比 lookup join 更准 |
| DWS 表数量 | 只保留流量总览 1 张 | 类目/会员等级 DWS 与 ADS 口径重复，留到 Sprint 3 与维表 join 一起做 |
| ADS 表形态 | 3 张宽表（交易/流量/类目） | 交易域指标共享同一窗口骨架，宽表一次查询即可；空窗口语义在写入侧统一 |
| 可加指标的空值 | 补 0 | 1 分钟窗口是固定骨架，"这一分钟没有支付"就是 0，不是"未知" |
| 时间语义 | 事件时间 + 5 秒水位线 | 乱序/重放结果一致；不用处理时间 |
| 验收对账口径 | 精确相等 | 事件时间即业务时间，逐窗求和必回到全量；能精确就不留"允许偏差" |

### 待办

- [ ] 源 topic 改为按 `event_id` 去重，使实时链路对「重复生产同一批事件」也幂等
      （当前 `earliest-offset` + 重复生产会让窗口指标累加）
- [ ] 给窗口/去重状态配置 `table.exec.state.ttl`
- [ ] Sprint 3 用 Spark 产出同名指标，与实时 ADS 交叉对账
- [ ] 维表 join（`dim_product` / `dim_user`）与 `dws_trade_user_level_1m`

---

## 2026-09-26 Sprint 6（服务层，顺序前移）

**主题**：数据后台（只读 API）+ 前后端（数据看板），直接部署在宿主机
**状态**：✅ 已上线并验收通过
**访问地址**：<http://36.151.150.140/data/>（接口文档 `/data/api/docs`）
**设计文档**：[`docs/sprint/SPRINT_6.md`](sprint/SPRINT_6.md)

### 阶段 1：先解决"要不要用 Docker"这个问题

项目负责人提出"服务层能不能不用 Docker、直接装在服务器上"。结论与理由：

```text
数据层（MySQL/Kafka/Doris/Flink/MinIO）→ 继续用 Docker Compose
服务层（Nginx + FastAPI + 前端静态文件）→ apt + systemd + Nginx 直装
```

数据层手工安装 Doris/Kafka 属于**高风险零收益**（版本绑定、依赖多、
且它已经用命名卷稳定跑了两个 Sprint）；而服务层只有两个进程，
systemd 管理反而更简单：`journalctl -u` 看日志、`nginx -t` 校验配置、
`systemctl restart` 重启，少一层容器网络与端口映射。

### 阶段 2：后端（services/api）

```text
config.py 口令只从 .env 注入（缺失就拒绝启动）
sqlguard.py 唯一允许构造 SQL 的入口：仅 SELECT / 表白名单 / 强制 LIMIT
doris.py 只读账号 + 参数绑定 + 查询超时 + DECIMAL 转字符串保精度
repository.py 所有 SQL 常量（KPI / 时间序列 / 明细 / 元数据）
metrics_doc.py 运行时解析 sql/metadata/metrics.md 作为口径字典
envelope.py 统一信封 {data, source, generated_at}：每个响应都能说明数据来源
main.py 11 个只读接口 + 统一异常处理（400/404/503 都是中文提示）
```

### 阶段 3：前端（services/web）

**无构建步骤**：Vue 3 全局构建 + ECharts 由 `install-web.sh` 下载到
`services/web/vendor/`（约 1.1 MB，不入 Git），源码即产物。
6 个页面：总览 / 交易分析 / 流量分析 / 类目销售 / 订单明细 / 指标口径。

前端三条与口径一致的约定：金额按字符串补零加千分位（跨行累加先转整数分）、
`null` 显示 `—` 而不是 0、每个页面底部显示数据来源（`source.tables`）。

### 阶段 4：部署

```text
scripts/install-web.sh   apt 装 nginx → venv 装依赖 → 建 dpapi 用户
                        → 建 Doris 只读账号 agent_ro → 下载前端运行时
                        → 装 nginx 站点与 systemd 单元 → 自检
scripts/deploy-web.sh    日常更新：检查文件 → 同步依赖 → 重载配置 → 重启 → 自检
scripts/verify-sprint-6.sh  7 步验收（状态/文件/Nginx 路径/健康/对账/安全/测试）
```

### 阶段 5：踩坑与修复

| # | 现象 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 创建只读账号时脚本在最后一行中断 | Doris **不支持** MySQL 的 `FLUSH PRIVILEGES`（`mismatched input 'FLUSH'`），且 GRANT 立即生效 | 去掉该语句；在 SQL 文件里写明原因（否则后人还会加回来） |
| 2 | 指标口径字典里所有指标的"域"都变成"通用" | 域标题正则 `\d+(?:\.\d+)?\s*` 没吃掉编号后的点（`## 2. 交易域指标`），整条正则永不命中 | 正则补 `\.?`；并给单元测试**加上域断言**（原来没断言所以一直没暴露） |
| 3 | 口径字典里 `gmv` 的表显示成类目表 | 同一字段名在交易域与类目域都存在，按字段索引时后者覆盖了前者 | `by_field()` 改为保留**文档中先出现**的主口径 |
| 4 | 口径字典里大量指标"所在表"=「同上」 | 文档表格用「同上」省略重复表名，解析器原样保留，血缘信息不可用 | 解析时把「同上」还原成同表上一行的真实表名 |
| 5 | 总览"下单用户数/UV"是 6000 / 19999，明显偏大 | 窗口表的 `order_user_cnt` / `uv` 是**每个窗口内**的去重值，逐窗求和得到的是"人次" | KPI 改为回到 DWD 明细 `COUNT(DISTINCT user_id)`（真实为 1195 / 1200），并写明"不能用逐窗求和" |
| 6 | 比率/客单价出现 8648.395961 这种长小数 | 除法结果未收敛精度，与口径定义的 `DECIMAL(18,2)` / `DECIMAL(10,4)` 不一致 | SQL 里显式 `CAST(... AS DECIMAL(...))` |
| 7 | `source.tables` 里同一张表重复出现 | 一个接口由多条查询拼成，直接拼接表名列表 | 信封里对表名与口径去重（保持顺序） |
| 8 | 脚本打印的访问地址是 `172.16.0.10`（内网） | `hostname -I` 返回的是云服务器内网地址 | `scripts/lib/common.sh` 新增 `public_ip()`：显式变量 → 公网回显服务 → 内网兜底 |
| 9 | 接口冒烟测试报 `UnicodeEncodeError` | 测试直接拼中文查询参数（类目名），urllib 不会自动 URL 编码 | 测试里对 path 做 `urllib.parse.quote` |
| 10 | **看板 6 个页面的 ECharts 图表全都不渲染**（KPI 与表格正常） | 真机 CDP 插桩结论：`draw` 被调用 8 次、`getEl()` 正常返回元素，但**每次容器尺寸都是 0x0**（页面刚挂载时容器不可见），于是在尺寸检查处早退；而 ResizeObserver 只在 `if (!inst)` 分支创建、`relayout()` 又要求 `inst` 已存在 → **没有任何机制能把 draw 叫回来**，容器后来可见了也永远不会初始化 | 先建立观察者再做尺寸判断；首次 init 不再要求实例已存在；补有上限的兜底重试（rAF 30 帧 + 定时退避 40 次），渲染成功后清零；`settled` 标志避免"init 成功但数据未到"被误判为完成 |
| 11 | 指标口径页显示"没有匹配的指标定义"、条数为空 | 接口与前端对 `/meta/metrics` 的**响应结构理解不一致**：后端返回对象 `{metrics, conventions, version, updated_at, source_document}`（便于携带文档版本与通用约定），前端直接当数组用 | 前端统一走 `metricList(payload)` 取列表，并把口径文档版本/更新时间显示在"口径来源声明"里（顺带把"口径来自文档"这件事显式呈现） |

> 第 10 条是本次最有价值的排查：**"没有报错、数据也对、就是图不显示"**
> 这类问题用 `--screenshot` 截图只能看到"空白"，无法区分
> 「没渲染」与「截图截早了」。改用 CDP 在页面里插桩计数
> （`draw` / `elNull` / `noSize` / `inited`）才一次定位到根因。

### 阶段 6：验收结果

```text
✅ bash scripts/verify-sprint-6.sh     7/7 PASS
    服务状态 / 前端文件 / Nginx 对外路径 / API 健康 / 指标对账 / 安全验证 / 自动化测试
✅ 访问地址                            http://36.151.150.140/data/（公网可达）
✅ 接口文档                            http://36.151.150.140/data/api/docs
✅ 指标对账                            API GMV 51,890,375.77 == MySQL 51,890,375.77（精确到分）
✅ 安全验证                            只读账号建表被 Doris 拒绝；DELETE 返回 405；limit 超限返回 422
✅ 自动化测试                          pytest 55 单元（SQL 守卫 31 + 生成器 24）+ 17 接口冒烟
```

### 决策记录

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 服务层部署方式 | apt + systemd + Nginx，**不进 Docker** | 只有两个进程；数据层容器已有稳定收益，手工重装是高风险零收益 |
| 后端框架 | FastAPI（不用 Spring Boot） | 与 Flink/生成器同语言同 venv，避免为一个只读接口引入 JVM 技术栈 |
| 前端构建 | 无构建步骤（Vue 全局构建 + ECharts vendor） | 6 个页面、无第三方业务依赖；引入 Node 构建链的收益低于其成本 |
| 接口安全 | 数据库只读账号 + SQL 守卫 + 接口约束（三道锁） | 只在应用层校验是单点防御，一旦绕过守卫 root 账号足以 DROP 整库 |
| 口径来源 | 运行时解析 `metrics.md` | 口径只能有一份，接口返回的就是文档原文 |
| 金额传输 | DECIMAL 转字符串 | float 序列化会让 51890375.77 变成 …69999999 |
| 去重指标口径 | 回到 DWD 明细 `COUNT(DISTINCT)` | 窗口表逐窗求和是"人次"不是 UV（本次实际踩到） |

### 待办

- [ ] 接口鉴权（当前只读公开，知道 IP 即可访问）
- [ ] 离线链路结果接入同一 API，与实时指标并排对比（Sprint 2/3 之后）
- [ ] 需要时加连接池与 5 秒 TTL 缓存（当前每次请求都查 Doris，实测 <50ms）

---

## 待办与下一步

### 当前阻塞 / 需人工处理

| 事项 | 说明 |
| --- | --- |
| 本地 Docker Desktop 不可用 | `wsl -l -v` 仍报 `REGDB_E_CLASSNOTREG`，重启未生效或 WSL 特性未启用。**不影响开发**（服务器是运行环境） |
| 本地 git 全局代理失效 | `http.proxy=http://127.0.0.1:7890` 已不可用；推送需 `git -c http.proxy= -c https.proxy= push`，或修复代理后恢复 |

### 下一阶段：Sprint 1 —— Kafka + Flink + Doris 实时数仓

目标产出：

```text
Kafka ──► Flink ──► Doris (DWD/DWS/ADS)
                        │
                        ├── 实时 GMV
                        ├── 实时订单量
                        ├── 实时支付量
                        └── 实时用户数（UV）
```

工作项：

- [ ] 设计实时数仓分层与指标口径（写入 `sql/metadata/`）
- [ ] Doris 建 DWD/DWS/ADS 表
- [ ] Flink 作业：消费 Kafka → 解析 → 窗口聚合 → 写 Doris
- [ ] 实时指标验证（与 MySQL 对账）
- [ ] 冒烟测试覆盖实时链路
- [ ] 明确不引入 Spark / Airflow / Iceberg / Agent

---

## 日志维护模板

追加新记录时使用：

```markdown
## YYYY-MM-DD Sprint N

**主题**：
**状态**：

### 阶段 X：<做了什么>
- 关键决策与理由
- 遇到的问题与根因
- 验证结果（给出实际命令输出，不要只写"通过"）

### 决策记录
| 决策 | 选择 | 理由 |

### 待办
- [ ]
```
