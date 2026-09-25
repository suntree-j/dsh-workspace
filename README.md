# dsh-workspace

DeepSeek Harness 公开工作区（suntree-j）。

## 给 dsh 用

工作区路径：

```text
C:\Users\jsy28\Desktop\dsh-workspace
```

GitHub：https://github.com/suntree-j/dsh-workspace

---

## 目录结构

```text
dsh-workspace/
├── README.md                      本文件
├── .gitignore / .gitattributes    工作区级忽略与行尾规则
├── .deploy/                       腾讯云服务器环境部署脚本
└── intelligent-data-platform/     《基于 Lakehouse 与 AI Agent 的
                                   批流一体智能数据分析平台》
```

---

## 项目：intelligent-data-platform

《基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台》——
面向电商场景的批流一体数据平台，最终包含实时链路（Kafka → Flink → Doris）、
离线链路（Spark → Iceberg on HDFS/MinIO → Hive），
以及基于 LLM + LangGraph + MCP 的智能数据分析 Agent。

核心原则：**先工程，再智能。**

```text
数据可靠 → 数据准确 → 数据可查询 → 数据可治理 → Agent 使用数据
```

### 当前进度

| Sprint | 主题 | 状态 |
| --- | --- | --- |
| **0** | **项目初始化与基础数据环境** | 🔄 代码已交付，容器验证进行中 |
| 1 | Kafka + Flink + Doris 实时数仓 | 未开始 |
| 2 | Spark + Hive + HDFS | 未开始 |
| 3 | ODS / DWD / DWS / ADS | 未开始 |
| 4 | Airflow | 未开始 |
| 5 | Iceberg Lakehouse | 未开始 |
| 6 | Backend + Dashboard | 未开始 |
| 7 | LLM + Tool Calling | 未开始 |
| 8 | LangGraph Data Agent | 未开始 |
| 9 | RAG + Metadata | 未开始 |
| 10 | MCP | 未开始 |
| 11 | Data Quality + Monitoring | 未开始 |
| 12 | 测试 + 性能优化 | 未开始 |
| 13 | 毕业论文 + 答辩 | 未开始 |

Sprint 0 包含：MySQL 8.4.11 / Kafka 4.2.1 (KRaft) / MinIO / Doris 4.1.4 (FE+BE)、
Python 数据生成器、启停与健康检查脚本、冒烟测试、完整文档。

技术栈与版本选型见
[`intelligent-data-platform/docs/development-environment.md`](intelligent-data-platform/docs/development-environment.md)。

### 快速开始

```bash
cd intelligent-data-platform
cp .env.example .env

# 方式一：一条命令完成全部验收
bash scripts/verify-sprint-0.sh

# 方式二：分步执行
docker compose config
docker compose up -d
bash scripts/status.sh
bash scripts/health-check.sh
python -m pytest
```

完整说明见
[`intelligent-data-platform/README.md`](intelligent-data-platform/README.md)。

### 部署目标服务器

| 项目 | 值 |
| --- | --- |
| 实例 | 腾讯云轻量应用服务器 `lavm-txyjj7xzr7` |
| 规格 | 4 核 / 16 GB / 100 GB SSD |
| 系统 | Ubuntu 24.04.2 LTS |
| 公网 IP | `36.151.150.140` |
| 内网 IP | `172.16.0.10` |

服务器环境部署（Docker 安装 + 镜像源配置）见 [`.deploy/README.md`](.deploy/README.md)。

---

## 工作区约定

1. **行尾统一**：`.gitattributes` 强制 `*.sh` / `*.sql` / `*.yml` 为 LF。
2. **不提交敏感信息**：`.env` 已被忽略，仓库中只保留 `.env.example`。
3. **不提交运行时产物**：虚拟环境、缓存、数据快照均已忽略。
4. **提交信息规范**：`<type>: <描述>`（feat / fix / docs / chore / test / refactor）。

各子项目可在自己的 `AGENTS.md` 中定义更细的规范。
