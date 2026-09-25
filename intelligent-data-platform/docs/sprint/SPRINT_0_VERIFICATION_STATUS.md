# Sprint 0 验收状态（Verification Status）

> 更新日期：2026-09-26
> 对应任务书：[`SPRINT_0.md`](SPRINT_0.md) 第 22、29 节

本文档如实记录 Sprint 0 各项 Definition of Done 的**实际验证状态**，
区分「已实测通过」与「待实际执行」，避免把未验证项当成已验证项。

---

## 0. 当前结论

```text
代码 / 配置 / SQL / 文档：已完成并提交
静态验证：已完成（compose config、bash 语法、pytest 单元测试）
容器运行时验证：待执行
```

**阻塞原因**：本机原先没有任何容器运行时。Docker Desktop 4.91.0 已通过 winget
安装成功，但启用 WSL2 / VirtualMachinePlatform 组件需要**重启系统**
（注册表 `Component Based Servicing\RebootPending` 已置位）。
重启前 Docker daemon 无法启动，因此所有依赖容器的验证项**尚未执行**。

> 这不是代码问题，是环境前置条件问题。

---

## 1. 恢复验证的方法

重启系统 → 启动 Docker Desktop（确认 **WSL 2 backend**，等待 **Engine running**）→ 执行：

```bash
cd intelligent-data-platform
cp .env.example .env      # 若尚未创建
bash scripts/verify-sprint-0.sh
```

该脚本会依次执行：环境检查 → `docker compose config` → `up -d` → `ps` →
`health-check.sh` → `pytest`，并输出 PASS/FAIL 汇总。

也可分步执行：

```bash
docker compose config
docker compose up -d
docker compose ps
bash scripts/health-check.sh
python -m pytest
```

> 首次运行需拉取约 5 GB 镜像；Doris 首次启动初始化元数据 + 注册 BE，
> 通常需要 1~3 分钟。

---

## 2. Docker Compose 编排

| 验收项 | 状态 | 依据 |
| --- | --- | --- |
| `docker compose config` 通过 | ✅ **已实测** | 退出码 0 |
| 7 个服务正确解析 | ✅ **已实测** | `config --services` |
| 镜像版本全部固定（无 `latest`） | ✅ **已实测** | `config --images` |
| 7 个命名卷正确声明 | ✅ **已实测** | `config --volumes` |
| Doris `FE_SERVERS` / `BE_ADDR` 为 IPv4 | ✅ **已实测** | 渲染结果 `fe1:172.28.0.10:9010` |
| Doris 静态 IP 与 `priority_networks` 网段一致 | ✅ **已实测** | 均为 `172.28.0.0/24` |
| Kafka 双监听器配置正确 | ✅ **已实测** | 渲染结果符合预期 |
| 网络 `data-platform` 子网 | ✅ **已实测** | `172.28.0.0/24` |

## 3. 容器运行时（待执行）

| 验收项 | 状态 |
| --- | --- |
| Docker daemon 可用 | ⏳ **待执行** |
| `docker compose up -d` 成功启动 5 个核心容器 | ⏳ **待执行** |
| `docker compose ps` 显示 healthy | ⏳ **待执行** |
| MySQL 健康 | ⏳ **待执行** |
| Kafka 健康 | ⏳ **待执行** |
| MinIO 健康 | ⏳ **待执行** |
| Doris FE 健康 | ⏳ **待执行** |
| Doris BE 健康 | ⏳ **待执行** |
| `scripts/health-check.sh` 输出全部 `[OK]` 且退出码 0 | ⏳ **待执行** |
| `scripts/start.sh` / `stop.sh` / `status.sh` 实际运行 | ⏳ **待执行** |

## 4. 初始化脚本执行结果（待执行）

| 验收项 | 状态 |
| --- | --- |
| MySQL `ecommerce` 库创建成功 | ⏳ **待执行** |
| MySQL 5 张业务表创建成功 | ⏳ **待执行** |
| MySQL 外键约束生效 | ⏳ **待执行** |
| Kafka 4 个 Topic 创建成功（分区 3 / 副本 1） | ⏳ **待执行** |
| MinIO `lakehouse` bucket 创建成功 | ⏳ **待执行** |
| Doris `ecommerce` 库创建成功 | ⏳ **待执行** |
| Doris `test_connection` 表创建成功且可查询 | ⏳ **待执行** |
| Doris BE 注册成功且 `Alive=true` | ⏳ **待执行** |

## 5. 数据生成器

| 验收项 | 状态 | 依据 |
| --- | --- | --- |
| 生成器代码完成 | ✅ **已完成** | `data-generator/src/` |
| 依赖在 Python 3.13 可安装 | ✅ **已实测** | Faker 39.1.0 / mysql-connector-python 9.7.0 / confluent-kafka 2.15.1 |
| 业务关系正确（外键、金额、时间因果） | ✅ **已实测** | 24 个单元测试全部通过 |
| 行为漏斗逐级收窄 | ✅ **已实测** | 单元测试断言 |
| 生成规模满足要求（1200/600/6000） | ✅ **已实测** | 本地实跑 |
| 实际写入 MySQL | ⏳ **待执行** | 需要容器 |
| 实际发送 Kafka 事件 | ⏳ **待执行** | 需要容器 |

## 6. 测试

| 验收项 | 状态 | 依据 |
| --- | --- | --- |
| 单元测试通过（24 个，不需要容器） | ✅ **已实测** | `24 passed` |
| 冒烟测试可正确收集（27 个） | ✅ **已实测** | `--collect-only` |
| 无容器时冒烟测试优雅跳过 | ✅ **已实测** | `24 passed, 27 skipped` |
| 冒烟测试全部通过（含 Kafka 生产/消费、Doris 查询） | ⏳ **待执行** | 需要容器 |
| Bash 脚本语法检查 | ✅ **已实测** | `bash -n` 全部通过 |
| 脚本在 daemon 不可用时正确 `exit 1` | ✅ **已实测** | 实测退出码 1 + 明确提示 |

## 7. 安全

| 验收项 | 状态 | 依据 |
| --- | --- | --- |
| 未提交 `.env` | ✅ **已实测** | `git check-ignore` 命中 |
| 未提交真实口令 / API Key / LLM Token | ✅ **已实测** | `.env.example` 全为 `change_me_*` |
| `.env.example` 已提交且不含真实凭据 | ✅ **已实测** | `git ls-files` |
| 运行时产物未入库（快照、venv、缓存） | ✅ **已实测** | `.gitignore` / `state/.gitignore` |
| Shell 脚本为 LF 行尾（跨平台可执行） | ✅ **已实测** | 逐字节校验，CRLF=0 |

## 8. 持久化（待执行）

| 验收项 | 状态 |
| --- | --- |
| `docker compose down` 后数据保留 | ⏳ **待执行** |
| `docker compose up -d` 后数据仍存在 | ⏳ **待执行** |

> 设计上已使用命名卷（`data-platform-*`），预期可满足；
> 但**必须实测确认**后才算验收通过。

---

## 9. 复现方式

本文档中「已实测」结论的复现命令：

```bash
# 编排校验
docker compose config --quiet

# 单元测试
python -m pytest -m unit

# 脚本语法与失败行为
bash -n scripts/*.sh scripts/lib/common.sh
bash scripts/health-check.sh; echo "exit=$?"

# 行尾校验（应为 CRLF=0）
file scripts/health-check.sh
```

---

## 10. 更新要求

每次执行 `bash scripts/verify-sprint-0.sh` 后，**必须回来更新本文档**，
把实际执行结果从「待执行」改为「已实测」或「失败」并附上证据。

> 依据 `AGENTS.md` 第 8 节：**禁止把未验证项写成已验证项。**
