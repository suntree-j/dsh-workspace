# 开发日志（Development Log）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 用途：按时间顺序记录**每个阶段做了什么、验证了什么、遇到什么问题、做了什么决策**。
> 维护要求：每完成一个阶段（代码 / 测试 / 验证 / 文档）就追加一条，见 `AGENTS.md` 第 14 节。

---

## 目录

- [2026-09-26 Sprint 0](#2026-09-26-sprint-0)
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
