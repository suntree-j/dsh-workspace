# 部署基线与版本清单

- 采集时间：**2026-11-16 04:07–04:30（Asia/Shanghai）**
- 采集方式：本机 `git` + 服务器只读命令（`systemctl show`、`docker compose ps`、`sha256sum`、`curl` 本机 nginx 入口）
- 用途：交接与部署的**唯一事实来源**。任何"线上是什么版本"的问题以此为准，不以记忆或历史文档为准。

## 一、本地 Git

| 项 | 值 |
| --- | --- |
| 工作树顶层 | `C:/Users/jsy28/Desktop/dsh-workspace`（项目在 `intelligent-data-platform/` 子目录） |
| 采集时 HEAD | `9f7286607d07d660ec3303dfa70c83a7b1aea5c6` |
| 本轮分支 | `opt/20261116`（从 HEAD 切出，隔离开发） |
| 相对 `origin/main` 引用 | 领先 **10** 个提交（远端 ref 未更新，见 §六） |
| 采集时未提交 | 仅未跟踪文件：交接包、`docs/thesis/build_proposal_slides.py`、两份答辩 docx/pdf、两份介绍 pdf；无已跟踪文件被修改 |

## 二、服务器

| 项 | 值 |
| --- | --- |
| 主机 | `lavm-txyjj7xzr7` / `36.151.150.140` |
| 系统 | Ubuntu 24.04.2 LTS，内核 6.8.0-53，4 核 / 16 GB / 99 GB |
| 项目目录 | `/opt/data-platform`（**没有 .git**，`git rev-parse` 报 not a git repository） |
| 公网入口 | `http://36.151.150.140/data/` → 200 |

## 三、服务与运行方式

| 单元 | 状态 | 运行方式 | 工作目录 | 配置文件 |
| --- | --- | --- | --- | --- |
| `nginx` | active | 系统包 nginx 1.24，`sites-enabled/data-platform.conf` | — | `/etc/nginx/sites-enabled/data-platform.conf` |
| `data-platform-api` | active | `/opt/data-platform/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --app-dir services/api` | `services/api` | 环境变量 `DATA_PLATFORM_ENV=/opt/data-platform/.env` |
| `data-platform-agent` | active（2026-11-11 01:15 启动） | `/opt/data-platform/.venv-agent/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100` | `services/agent` | 同上 |
| `data-platform-mcp` | active | `/opt/data-platform/.venv-agent/bin/python -m app.main --transport streamable-http` | `services/mcp` | `MCP_DATA_API_BASE=http://127.0.0.1:8000`、`MCP_PORT=8200` |
| `data-platform-airflow-apiserver` / `-scheduler` / `-dagprocessor` | 三个均 active | 各自 venv `.venv-airflow` | `airflow` | `AIRFLOW_*` 环境变量 |

数据访问路径：API 用只读账号 `agent_ro` 连 Doris（`/data/api/health` 返回 `readonly_enforced: true`）；Agent 通过 HTTP 调 API（`/data/agent/health` 返回 `data_path.path = http`），**当前不走 MCP**。

运行中依赖版本（实测）：

| venv | Python | 关键包 |
| --- | --- | --- |
| `.venv`（API） | 3.12.3 | fastapi 0.141.1、uvicorn 0.54.0、pydantic 2.13.5、httpx 0.28.1、requests 2.34.2 |
| `.venv-agent`（Agent/MCP） | 3.12.3 | fastapi 0.141.1、uvicorn 0.54.0、langgraph 1.1.0、mcp 2.2.0、pydantic 2.13.5 |

## 四、容器与链路

14 个容器全部 Up：doris-fe、doris-be、mysql、kafka、minio、hive-metastore、spark-master、spark-worker、flink-jobmanager、flink-taskmanager、flink-sql-gateway、flink-jobs、grafana、prometheus。

- Flink REST：**8 / 8 个作业 RUNNING**。
- `flink-*` 容器采集时 Up 24 分钟（说明本轮重启/恢复发生过，见 D2 的恢复期证据）。
- 健康端点（经本机 nginx）：`/data/` 200、`/data/api/health` 200、`/data/agent/health` 200、`/data/api/batch/reconcile` 200。

## 五、部署文件清单（218 个跟踪文件中的差异 7 项）

对比方法：工程目录 git 跟踪文件 + `docker-compose.yml`，CRLF→LF 归一化后取 SHA-256。差异项如下（本地哈希为 LF 归一化值，与服务器同法可比）：

| 文件 | 本地 | 部署（采集时） | 判定 | 处理 |
| --- | --- | --- | --- | --- |
| `scripts/real-data/build_uci_dataset.py` | `f760bb21ff7f1e0d…` | `bc0211952836ed98…` | 本地可配置窗口、默认全量；服务器写死 2011-11 | **已部署本地版**（同窗口对照：输出逐行相同） |
| `scripts/real-data/build_tianchi_dataset.py` | `9cca081d63de6f44…` | `162835b8ee2c1dc1…` | 服务器版商品主键会重复 356 行 | **已部署本地版** |
| `infrastructure/quality/checks.conf` | `ca82f3dc047e2da0…` | `af9c8867ed0ff26e…` | 服务器版注释仍写死旧合成值 51,890,375.77（仅注释） | **已同步**（2026-11-16 05:45，两侧 MD5 `6eef4537aad58898d71b967760274107`；服务器已无该常量） |
| `services/api/app/doris.py` | `b4a384982cdd66c7…` | `5b935814a3d1e84e…` | 仅文档字符串示例金额不同 | **已同步** + 重启 api（两侧 MD5 `e5f3f275878f7358d00a24382f3a1953`，health/overview 均 200） |
| `data-generator/README.md` | `c311d25c750cf0e1…` | `5159b29e1fad8953…` | 本地有【现状说明】块 | **已同步**（两侧 MD5 `465fdee9e5aebf2120258cac21fa56ca`） |
| `sql/metadata/kafka_topics.md` | `611d335900924eac…` | `69fcdcdd87c61f18…` | 本地补了真实单价口径说明 | **已同步**（两侧 MD5 `a959c63b858a59363e5cec70c2cc93ba`） |

> 状态更新（2026-11-16 05:45）：上表 6 项差异中，前两项在 D0 已部署，其余 4 项本次同步完毕，
> 服务器与本地**逐文件 MD5 一致**（部署后用 `systemctl restart data-platform-api` 让运行代码与部署文件一致）。
> `tests/test_frontend_quality_manifest.py` 仍不上线（服务器不跑测试）。
> 注意：表格里的「本地 / 部署」两列是 **D0 采集时**的 SHA-256（LF 归一化），用于复现当时的差异；
> 同步后的核验值是上表处理列里的 MD5，两种算法不要混读。
| `tests/test_frontend_quality_manifest.py` | `c5d92b79866a8307…` | **缺失** | 测试文件，服务器不跑测试 | 不上线 |

## 六、数据文件清单（`data-generator/data/real/`，服务器实测）

| 文件 | 大小 | SHA-256 | 对应构建脚本产物 |
| --- | ---: | --- | --- |
| `orders.csv` | 69,727,091 | `b96fe561cdd7e863…` | UCI 全量 |
| `payment.csv` | 46,980,973 | `fc98cb0225613ccf…` | UCI 全量 |
| `refund.csv` | 1,005,866 | `606183fabb427694…` | UCI 全量 |
| `product.csv` | 271,211 | `a0264e45840e7f9d…` | UCI 全量 |
| `user.csv` | 463,612 | `c4de86c4043b6d3a…` | UCI 全量 |
| `tianchi_behavior.csv` | 918,108,098 | `f9b0037fbe9e3d77…` | 天池 10 万用户 × 9 天 |
| `tianchi_product.csv` | 98,447,014 | `2f2f02379a0c321b…` | 天池（**去重版**，与当前脚本输出逐字节相同） |
| `tianchi_user.csv` | 8,591,050 | `64f12bd439721b75…` | 天池 |

> `tianchi_product.csv` 的哈希与「当前本地脚本 + 同一输入」的重新构建产物**完全一致**，这是"已装载数据由新版脚本生成"的直接证据。详见 `docs/evidence/D0_数据制备版本对照.md`。

## 七、与远端仓库的偏离

- 本地 `opt/20261116` 领先 `origin/main` 引用 10 个提交；`github.com:443` 从本机不通（`Connection was reset`），`api.github.com` 与 `github.com:22` 可达。
- 恢复推送的两条路径：①用户把 `~/.ssh/id_ed25519.pub` 加入 GitHub → `git push github-ssh main`；②开代理走 `origin`（`git -c http.proxy= … push`，需代理在监听）。
- 在推送恢复前，**服务器与本地的差异以本文件 §五 为准**，不要用 `git pull`（服务器不是仓库）。

## 八、回滚与恢复

| 场景 | 做法 |
| --- | --- |
| 脚本回滚 | 从 `git log` 取旧提交覆盖对应文件，再 `scp` 回去；无需动数据 |
| 数据库回滚 | `/opt/data-platform/.backup/20261106_042632/`（`mysql_ecommerce.sql` 1.2M、`mysql_hive_metastore.sql` 5.8M、`doris_ddl.sql` 32K、`minio_warehouse.tar.gz` 223M） |
| 服务回滚 | 单文件 `scp` + `systemctl restart data-platform-<svc>`；前端只涉及 `services/web/{index.html,app.js,styles.css}` |
| 不可逆动作 | 重放 Flink、删 Kafka topic、TRUNCATE Doris 表 —— 见 `docs/服务器连接与目录说明.md` §9 |
