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

## 2026-09-26 Sprint 2（离线链路）

**主题**：Spark + Hive + 湖仓存储（MySQL 全量抽取 → Parquet on S3A → Hive 外部表）
**状态**：✅ 已实现并验收通过（8/8 PASS）
**设计文档**：[`docs/sprint/SPRINT_2.md`](sprint/SPRINT_2.md)（含 11 条踩坑记录）

### 阶段 1：先算内存，再决定装什么

上新组件前先做资源侦察，结论直接改变了方案：

```text
宿主机 15.99 GB，但实测**可用只有 2.0 GB**：
  Doris FE 容器 7.27 GB（含页缓存）／Flink 三容器 2.46 GB／Kafka 1.13 GB
若再加 Spark(≈2.5 GB) + Hive(≈0.8 GB) + HDFS(≈1.3 GB) → 必然 OOM，
而 OOM 会连带打挂已经验收过的实时链路与看板 —— 这个代价不可接受。
```

于是做了两件事：**先降配，再上新组件**。

| 组件 | 原值 | 新值 | 说明 |
| --- | --- | --- | --- |
| Kafka | `-Xmx1G` | `-Xmx512m` | 只有 12 个 topic、每个 3 分区 |
| Flink TaskManager | 3072m | 2048m | 8 个作业各 1 slot |
| Flink JobManager | 1024m | 768m | — |
| Doris FE | `-Xmx1536m` | `-Xmx1024m` | 只服务看板的只读查询 |

**效果：可用内存 2.0 GB → 9.0 GB**，且降配后实时链路回归验证全部通过
（health-check 11/11、数据服务 GMV 一字不差）。

### 阶段 2：存储层从 HDFS 换成 S3A（记录在案的偏差）

设计目标架构写的是 `Iceberg on HDFS/S3`，本次选 **S3A(MinIO)**：
MinIO 在 Sprint 0 就是为湖仓准备的（bucket 名 `lakehouse`），
而 HDFS 要多占 1.3 GB 且与"保实时链路"冲突。
补回 HDFS 的路径（加两个服务 + 改 `fs.defaultFS`）已写进 SPRINT_2.md 第 2.2 节。

### 阶段 3：离线链路的实现

```text
MySQL（唯一事实源）
   └─ Spark JDBC（按主键 range 并行读，显式 schema 转换型）
        └─ Parquet（snappy）→ s3a://lakehouse/warehouse/ods/<表>/
             └─ Hive Metastore 目录 → lakehouse.ods_*（EXTERNAL TABLE，DROP 不删数据）
                  └─ 作业内自带逐表对账（不一致就退出码非 0）
```

产物：`docker-compose.yml` 新增 4 个服务（metastore/master/worker + 按需 submit）、
`infrastructure/spark`（Dockerfile + spark-defaults + 抽取作业）、
`infrastructure/hive`（hive-site.xml）、`sql/hive/01_ods_tables.sql`、
3 个脚本（init-lakehouse / submit-offline-job / spark-sql）+ verify-sprint-2。

### 阶段 4：踩坑与修复（本 Sprint 的主要工作量，共 11 条）

最难的是 **Hive 客户端与服务端的版本契约**，试了三种组合才找到可用解：

| 尝试 | 结果 |
| --- | --- |
| Metastore 4.0.1 + Spark 内置客户端 2.3.9 | ❌ `Invalid method name: 'get_table'`（Hive 4 删了旧 thrift 方法） |
| 让 Spark 用 Hive 4 客户端（jars=path） | ❌ Spark 对 `metastore.version` 有白名单（≤3.1.3）；`jars.path` 在 `fs.defaultFS=s3a` 下还被当成 S3 路径 |
| 声明 `metastore.version=3.1.3` + 内置客户端 | ❌ `Builtin jars can only be used when hive execution version == hive metastore version` |
| **Metastore 3.1.3 + 不声明版本（内置 2.3.9 客户端）** | ✅ 成功 |

其余 10 条（XML 注释里的双连字符、entrypoint 每次 initSchema 导致崩溃重启、
entrypoint 忽略命令行参数拿 Derby 脚本初始化 MySQL、Metastore 缺 S3A 类、
spark-work 卷属主 root、MasterUI 只绑主机名、`set -e` 下 grep 无匹配静默退出、
Spark `/json/` 美化输出导致解析失败、`compose run` 容器名不可解析、
S3 上没有"目录"导致事件日志目录校验失败）逐条记在 SPRINT_2.md 第 6 节，
每条都写了根因与修法，避免后人重踩。

### 阶段 5：验收结果

```text
✅ bash scripts/verify-sprint-2.sh      8/8 PASS
✅ 逐表对账                              ods_user 1200 / product 600 / orders 6000
                                        payment 5406 / refund 254 —— 与 MySQL 精确一致
✅ 存储落地                              25 个 Parquet 对象在 s3a://lakehouse/warehouse/ods
✅ 类型正确                              amount decimal(18,2)，ODS 无 double/float
✅ 幂等                                  重跑抽取作业后行数不变
✅ 回归                                  实时链路 11/11 健康、数据服务正常
```

### 决策记录

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 是否上 HDFS | 不上，用 S3A(MinIO) | 内存实测只够一个；且 MinIO 本就是为湖仓准备的，Sprint 5 的 Iceberg 同时支持两者 |
| 是否上 HiveServer2 | 不上，只跑 Metastore | Spark 直接访问 Metastore 即可；HS2 要多一个数 GB 的进程 |
| Hive 版本 | 3.1.3（不是最新的 4.0.1） | 与 Spark 内置客户端兼容；「最新」不等于「可用」 |
| 抽取方式 | JDBC 分区读 + 显式 schema + 作业内对账 | 数据量小但方法要可复制；对账放作业里，每次运行自带证据 |
| 表类型 | EXTERNAL TABLE | DROP 只删元数据，重跑不会误删数据 |
| 端口/资源 | 每个新服务显式 `mem_limit` | 宁可单容器 OOM 也不拖垮宿主机 |

### 待办

- [ ] `tests/smoke/test_offline.py` 随下一次全量冒烟执行确认
- [ ] Sprint 3：在 `ods_*` 上建 DWD/DWS/ADS，并与实时指标交叉对账

---

## 2026-09-26 看板 UI 设计升级（Sprint 6 增强）

**背景**：Sprint 6 的看板功能可用，但设计偏"能跑就行"。本次做一次整体设计升级。

**做法**：把 UI 当成有设计系统的产品来做，而不是堆样式：

1. **设计令牌化**：色板 / 间距刻度 / 圆角 / 阴影 / 字号阶梯 / 过渡时长全部收敛到
   `:root` 变量，后续规则只引用变量 —— 这是"设计系统"最小可讲的形态。
2. **信息层级**：顶栏（面包屑 + 页面标题 + 数据源徽标 + 数据时间 + 刷新）、
   左侧导航、内容区；KPI 数字用独立字号令牌当视觉主角，口径说明收进提示。
3. **诚实的数据表达**：KPI 环比徽标只在**最近两个窗口都有非零值**时显示，
   否则显示"待下一窗口" —— 实时链路没写满时不该给出 -100% 这种假信号。
4. **图表统一主题**：新增 `CHART_THEME`，网格/坐标轴/图例/提示框/调色板集中一处；
   折线带渐隐面积、柱状圆角、类目条形带数值标签；空数据显示"图标 + 说明 + 建议"。
5. **可达性**：skip-link、`aria-current`、图标按钮 `aria-label`、
   弹窗焦点归位、涨跌除颜色外还有 ▲/▼ 与 sr-only 文案、`prefers-reduced-motion` 关动画。
6. **一个实测出来的取舍**：KPI 卡片固定 **≤3 张一行**。
   1600px 视口下内容区 1220px，4 列时每卡内容宽 374px，而
   `1,283,809.29 元` 在该字号下需 373px —— 刚好卡在临界点，换个数据就折行。
   于是字号改成"按一行几张卡给定"并加 `nowrap`：宁可字号小一点，也不让金额折行。

**验证**：真实浏览器逐页检查（CDP）：6 个页面全部渲染，每个 `.chart` 容器
`canvas ≥ 1`（总览 3/3、交易 1/1、流量 2/2、类目 2/2），无 JS 异常；
另在本地用桩数据验证了空态、错误态、骨架屏与窄屏抽屉布局。

---

## 2026-09-26 Sprint 3（离线分层建模 + 批流交叉对账）

**目标**：把 Sprint 2 落地的 ODS 层往上建成 ODS → DWD → DWS → ADS 四层，
并回答本项目最核心的一个问题 —— **同一条业务事实，走实时链路与走离线链路，
算出来的指标是不是同一个数。**

**结论（先给结果）**：是同一个数。
11458 个分钟窗口逐条比对，不一致 **0** 个；GMV 实时 `51,890,375.77`
== 离线 `51,890,375.77`，精确到分。

### 阶段 0：事故 —— 批处理打穿宿主机内存，整机失联（本 Sprint 最大的教训）

第一次跑分层批处理时，服务器在几分钟内完全失去响应：SSH 建立不了会话、
ICMP 无响应、看板与接口全超时，只能从云控制台**强制重启**。

三层原因叠加：

1. 实时链路常驻约 **13.7 GB**，16 GB 的机器空闲只剩约 2.0 GB；
2. Spark 驱动在 client 模式下跑在**宿主机**上，默认 1 GB 堆，加执行器 1 GB
   —— 正好把余量吃干；
3. 宿主机**没有 swap**，内核无处回收页面，内存压力不是"变慢"而是"整机假死"。

第一现场其实是 Doris BE 拒绝查询（看板报"数据仓库暂时不可用"）：

```text
[MEM_ALLOC_FAILED] ... sys available memory 510.64 MB, low water mark 799.46 MB
```

当时误以为是 Doris 的偶发问题，没有立刻停下来算内存账，才演变成整机失联。

**处理（四层防护，从"事前拒绝"到"事后兜底"）**：

| 层 | 措施 | 文件 |
| --- | --- | --- |
| 1 拒绝启动 | 可用内存 < 3000 MB 直接拒绝跑批；已有作业时拒绝叠加 | `scripts/lib/memory-guard.sh` |
| 2 错峰执行 | 跑批前暂停 Flink 栈（释放约 2.8 GB），跑完自动恢复并验证健康 | `scripts/batch-mode.sh` |
| 3 限制上限 | 驱动/执行器各 768m；`spark-submit` 容器补 `mem_limit`（原先无上限） | `scripts/lib/spark-job.sh`、`docker-compose.yml` |
| 4 兜底 | 4 GB swap（swappiness=10）给内核留回收空间；**故意不写 fstab**（Doris BE 不允许在有 swap 的机器上启动） | `scripts/setup-swap.sh` |

留下的教训：**在一台已经跑满的机器上，"再加一个批处理"不是加一个进程，
而是把系统的安全余量直接归零 —— 先算内存账，再动手。**

### 阶段 1：分层设计

```text
ODS（Sprint 2 已落地） → DWD（去重/清洗/补维） → DWS（按天轻度聚合） → ADS（指标口径）
                                                                        ↓
                                        Doris S3() TVF 直读 Parquet 装载 → 只读服务查询
```

三个关键设计决定：

1. **ADS 同时出 1 分钟与 1 天两套表**。实时 ADS 是分钟粒度，
   若离线只出天粒度，两者根本无法逐条比对，"批流对账"就只能停留在口号上。
   1m 用于对账，1d 用于报表 —— 不让看板把 11000+ 行分钟数据拉回来自己聚合。
2. **DWS 只出可加指标**。比率（客单价/成功率/退款率）一律留到 ADS
   按 `metrics.md` 的公式算 —— 否则会出现"两个层各写一份比率公式"的口径分裂。
3. **离线指标单独一个库 `lakehouse_ads`**。`ecommerce` 里是实时链路的表，
   两套链路同库同名会让人靠记忆判断"这个数是谁算的"，分库之后库名即来源。

### 阶段 2：离线结果如何进 Doris —— 用 S3() TVF 而不是 Stream Load

Stream Load 要把文件推到 BE 的 HTTP 端口，就得再起一个"读 S3 → POST"的
loader 进程（多一个要维护、要监控、可能挂掉的东西）。
Doris 4.x 自带 `S3()` 表函数可直接读 MinIO 上的 Parquet，
一条 `INSERT INTO ... SELECT ... FROM S3(...)` 解决问题，**不引入任何新组件**。

### 阶段 3：踩坑与修复（共 10 条，完整版见 SPRINT_3.md 第 8 节）

按"最容易被误导"的顺序排列：

| # | 现象 | 真正的原因 |
| --- | --- | --- |
| 1 | 整机失联 | 内存叠加，见阶段 0 |
| 2 | 分层 SQL 挂载失败 | Docker 不允许在只读挂载点下再创建挂载点 |
| 3 | 断言报"少了一半数据"（2834 vs 5998） | `COUNT(DISTINCT x)` 是**近似算法**（5% 误差），不是数据错 |
| 4 | 断言报"用户数 5883 ≠ 1195" | 断言写错：去重计数不可加，逐日之和本就不等于全局去重 |
| 5 | `CREATE DATABASE ... COMMENT` 报语法错 | Doris 不支持这种写法 |
| 6 | TVF 读到 0 行 | `*.parquet` **不递归**进 `dt=` 分区目录，必须 `**/*.parquet` |
| 7 | 报 `Unknown column 'window_start'` | 是 6 的次生现象：源为空 → 拿不到列，报错信息把人引向"列名写错" |
| 8 | 装载报"事务里不允许" | Doris 显式事务只允许 insert/update/delete/commit/rollback，不能 TRUNCATE |
| 9 | 上述 5/8 排查多花好几轮 | 脚本里 `2>/dev/null` 把**真实错误**和无害警告一起吞了 |
| 10 | 类型断言全挂但字段明明是对的 | Spark 的 `DESCRIBE` 会给**列名右填充空格**，`$1=="amount"` 永远不成立 |

第 3、4、10 条都属于同一类问题：**断言本身写错了**。
它比"没有断言"更危险，因为它会把人引向"数据错了"的错误结论，
浪费时间去查一个并不存在的问题。因此修完之后统一在文档里写清
"为什么这么写"，而不只是把代码改对。

### 阶段 4：验收结果

```text
✅ bash scripts/verify-sprint-3.sh   （8 步验收）
```

| 步骤 | 结果 |
| --- | --- |
| 1 表清单 | 湖仓 ODS 5 / DWD 5 / DWS 3 / ADS 6；Doris `lakehouse_ads` 6 张 |
| 2 层间行数 | DWD == ODS 逐表相等；ADS 窗口 11459 == 事件分钟 11459 |
| 3 类型正确 | 无 double/float；金额 `decimal(18,2)`，比率 `decimal(10,4)` |
| 4 幂等 | 重跑 ADS 层后窗口数与 GMV 不变 |
| 5 **批流对账** | 窗口 **11458**，不一致 **0**；GMV 两侧均 `51890375.77` |
| 6 服务装载 | 6 张表 Doris 行数 == 湖仓；`agent_ro` 可查、写操作被拒 |
| 7 回归 | health-check 11/11；数据服务 `/health` ok；看板 200 |
| 8 自动化测试 | pytest 全部通过（用项目 `.venv` 解释器） |

### 阶段 5：看板新增「离线与对账」页

离线指标光算出来不够，还要能看见"它和实时是一致的"。

- 新增页面 `#/batch`：离线 KPI、按天趋势、类目 Top10、
  **实时 vs 离线并排对比表**（差异列 0 用中性色、非 0 用负向色加粗）、
  对账结论条（一致绿 / 有差异黄）、差异明细表；
- 新增接口 `/batch/overview` 与 `/batch/reconcile`；
- 服务层 `sqlguard.BUSINESS_TABLES` 显式加入 6 张离线表
  （白名单是权限边界，新增表必须显式登记）；
- 真实浏览器逐页复核：7 个页面 `canvas ≥ 1`（离线页 2/2），无 JS 异常。

### 决策记录

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 离线结果如何进 Doris | `S3()` TVF 直读 | 不引入外部 loader 进程；一条 SQL 可审计 |
| ADS 粒度 | 同时出 1m 与 1d | 只有同粒度才可能逐窗口对账 |
| 比率指标放在哪层 | 只在 ADS | 避免同一公式在两层各写一份 |
| 离线指标放哪个库 | 新建 `lakehouse_ads` | 库名即链路来源，不靠记忆区分 |
| 内存不够怎么办 | 错峰（暂停实时链路） | 不新增组件、不改架构，符合当前硬件条件 |
| 流量域离线化 | **本 Sprint 不做** | MySQL 无行为事实表，离线无源；不伪造，缺口写进文档与论文 |

### 待办

- [ ] 流量域（UV/PV/转化率）的离线化：Sprint 4 加"Kafka → 湖仓 ODS"归档作业
- [ ] 把离线流水线交给 Airflow 调度（Sprint 4）
- [ ] swap 是否写入 fstab 的最终决策（需先解决 Doris BE 的 swap 检查）

---

## 2026-09-26 Sprint 7（LLM + Tool Calling：数据问答 Agent）

**目标**：让数据"能被问"。自然语言进，带来源说明的回答出。

**结论**：验收 **49/49 通过，0 失败、0 跳过**；实测三问全部正确，
且每个回答都附"用了哪些表 + 实际执行的 SQL"，可逐条核对。

### 阶段 0：先定架构边界，再写代码

设计文档里写的是"Agent → SQL 安全检查 → Doris 只读查询"。
如果照着字面实现，安全检查会写在 Agent 进程里 —— 那等于**让被约束者自己当裁判**。

因此改成：

```text
Agent 进程里没有数据库凭据，也没有 mysql 依赖；
它只能经 HTTP 调只读数据服务，而那条通道受 SQL 守卫与只读账号约束。
```

**安全检查不再是一段代码，而是一条边界**：想绕过也没有入口。
这是本 Sprint 唯一一个真正重要的设计决定，其余都是它的推论。

### 阶段 1：只读查询节点 `POST /query`

这是项目里唯一**接受外部 SQL** 的接口，因此专门列了它的安全面：

| 防线 | 拦住什么 |
| --- | --- |
| `sqlguard.validate_select` | 非 SELECT / 多语句 / 注释 / DDL-DML / 白名单外的表；强制 LIMIT |
| 只读账号 `agent_ro` | 即使守卫被绕过，写操作仍被数据库拒绝 |
| 连接级 `read_timeout=15s` | 慢查询断开，不拖垮集群 |

两个细节值得记：

1. **返回 `executed_sql`**（守卫改写后的实际语句）。
   守卫会补/收 LIMIT，如果只返回调用方传来的那句，
   "我查了这句话"就不可核对 —— 而那正是「不得伪造查询结果」的落地方式。
2. **血缘表名由服务端从 SQL 提取**，与校验共用同一套正则。
   让调用方自报"读了哪些表"，这个字段就没有可信度了。

**为什么这个接口破例用 POST**：是长度问题不是语义问题。
LLM 生成的 SQL 常带多列与多个聚合，实测轻易超过 2KB，
放查询串会撞上各级代理的 URL 长度限制，出现"简单问题能问、复杂问题 502"。

### 阶段 2：Agent 实现

- **工具集合刻意最小**（4 个）：`metrics_lookup` / `tables_lookup` /
  `sql_query` / `reconciliation`。没有"列出所有表"这类工具 ——
  白名单固定，写进提示词比让模型多绕一轮更省也更稳。
- **工具失败不中断循环**，把错误文本交回模型让它改写（对应「查询失败必须能重新规划」）；
- **必然终止**：最多 6 轮，超限后强制收尾且收尾请求**不携带 tools**
  （否则模型还能继续发起调用）；
- **思考模式默认关闭**：DeepSeek 官方要求"带 tools 时历史轮次的
  `reasoning_content` 必须回传，否则 400"。对生成 SQL 这个场景，
  好处有限而代价明确（token 成倍、结果不可复现）。代码里
  `assistant_message()` 统一负责回传该字段，将来打开也不用改调用方。

### 阶段 3：部署与网关

- 两个 systemd 单元（`data-platform-api` / `data-platform-agent`），
  两个独立 venv —— 故障隔离、权限隔离、可独立重启；
- Nginx 新增两个精确 location：
  * `/data/api/query` **必须单独开**：外层 `/data/api/` 的
    `limit_except GET HEAD OPTIONS` 会把 POST 挡在 Nginx 层返回 403，
    FastAPI 根本收不到请求；
  * `/data/agent/` 反代到 8100，超时放到 180s（Agent 要串多次调用）。

### 阶段 4：踩坑与修复（7 条，完整版见 SPRINT_7.md 第 9 节）

其中三条属于**"断言/工具本身写错，却表现为产品缺陷"**，危害比没有断言更大：

| # | 现象 | 真正原因 |
| --- | --- | --- |
| 1 | 新增路由后 Agent 提问 404，服务状态却全正常 | 部署脚本只重启了 Agent，没重启数据服务 |
| 2 | 看板全页报错、所有请求 404 | 把 `API_BASE`（`/data/api`，绝对路径）当相对前缀又拼了一次 → `/data/api/api/...` |
| 3 | 验收报"拒绝原因不正确"，守卫其实工作正常 | FastAPI 输出**紧凑 JSON**（`"code":"X"`），断言写的是带空格的版本 |
| 4 | `printf ... \| grep -qF` 明明命中，`if` 里却是假（退出码 **141**） | `grep -q` 命中即退出，printf 收到 SIGPIPE；`pipefail` 下管道整体返回 141 |
| 5 | 归一化辅助函数用到源码上，`key: 'ask'` 匹配不上 | JSON 空白归一化会改坏源码文本，两个场景必须用两个函数 |
| 6 | 同一台机器两个 `app` 包无法同时导入 | 两个服务目录同名，`conftest.py` 统一让 agent 优先，跨服务交给 HTTP 测试 |
| 7 | 公网访问约 15~40% 概率 502 | 见下 |

第 4 条最隐蔽：同样的命令写在临时脚本里正常，写在验收脚本里失败 ——
因为只有后者 source 了 `common.sh`（`set -euo pipefail`）。

### 阶段 5：公网 502 的排查结论（不是我们的问题）

用户报"页面打不开（502）"。逐项排查：

| 判据 | 实测 |
| --- | --- |
| nginx 访问日志里的 5xx | 只有 22 条 **503**（早前 Doris 内存事故期间 API 返回的），**无 502** |
| `TcpExtListenOverflows` / `ListenDrops` | 0 / 0 |
| `TcpInErrs` | 0 |
| 服务端自测连续 5 次 | 全部 200 |
| `somaxconn` / listen backlog | 4096 / 511（充裕） |

**请求根本没到服务器** —— 在到达 nginx 之前就被中间链路拒了。
本机没有可修的东西。

**应对**：演示与验收改走 SSH 隧道
（`ssh -N -L 18080:127.0.0.1:80`），测的仍是真实部署，只是绕开公网抖动。
本 Sprint 的浏览器验收（8 个页面全部渲染正常）即用此方式完成 ——
直连公网时页面会随机 502，看起来像前端坏了，实际是链路问题。

### 阶段 6：端到端实测（三问）

| 提问 | 结果 |
| --- | --- |
| 最近一周每天的 GMV 是多少？ | 7 天明细 + 合计 422 万；正确选了**离线天表**，并主动提示"09-26 可能是未跑完的当天" |
| 支付成功率和退款率分别是多少？ | `97.80%`（5287/5406）与 `4.00%`；与 Sprint 1/3 验收数据**精确一致**，并自己说明了"先合计分子分母再相除"的口径 |
| 这些数据准不准？实时和离线一致吗？ | 调对账工具报 11458 窗口零不一致，并补了一句"UV 等去重指标不能跨窗口相加，不在本次对账范围内" |

第三问那句话很重要：它**没有假装流量域也对过账** ——
正好印证了 Sprint 3 记录的设计缺口，"不伪造"这条约束在真实回答里生效了。

### 决策记录

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 安全检查放哪 | 服务端 `sqlguard` | 放 Agent 里等于让被约束者自己当裁判 |
| Agent 如何取数 | 只经 HTTP 调只读服务 | 进程边界即权限边界，绕过没有入口 |
| `/query` 用 GET 还是 POST | POST | 长度问题；GET 放 SQL 会撞代理 URL 上限 |
| 思考模式 | 默认关闭 | 带 tools 时必须回传 reasoning_content；token 成倍、结果不可复现 |
| 模型 | `deepseek-flash` | 上下文短，不需要 pro；支持 Tool Calls，价格约 1/4.5 |
| 缺 Key 时 | 服务照常启动，`/ask` 返回 503 + 配置步骤 | "基础设施就绪"与"凭据就绪"分开推进，状态始终可见；但绝不假装回答 |
| 用不用 LangGraph | 不用（现在） | Sprint 7 目标是基础能力；图的状态机会掩盖"这轮调了什么工具"，不利于可验证性 |

### 待办

- [ ] 多轮对话与会话记忆（Sprint 8）
- [ ] 查询审计落盘（`steps` 目前只随响应返回，未持久化）
- [ ] 成本核算：统计每次提问的 token 与花费
- [ ] RAG：口径与表结构目前全量注入提示词，数据源变多后需要向量检索（Sprint 9）

---

## 待办与下一步

### 当前阻塞 / 需人工处理

| 事项 | 说明 |
| --- | --- |
| 本地 Docker Desktop 不可用 | `wsl -l -v` 仍报 `REGDB_E_CLASSNOTREG`。**不影响开发**（服务器是运行环境） |
| 公网访问偶发 502 | 客户端经公网访问 `/data/api/*` 时约 1/3 的请求返回 502（实测 6 次里 2 次），但**服务器 nginx 访问日志里没有任何 5xx**（`grep -cE ' 50[0-9] ' access.log` = 0），服务端自测（curl 127.0.0.1、经 nginx）全部 200，且失败在不同路径上随机分布。判断是公网链路中间设备导致，**不是应用问题**。若日后影响演示，可加 CDN 或改走 HTTPS |

> git 推送：本机 `http.proxy=http://127.0.0.1:7890` 可用，直接 `git push` 即可
> （若代理未启动，则需 `git -c http.proxy= -c https.proxy= push`）。

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
