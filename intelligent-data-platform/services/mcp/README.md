# services/mcp — 只读数据 MCP 服务（Sprint 10）

> 把「只读数据服务」的能力用 **MCP（Model Context Protocol）** 暴露出来，
> 让 Agent（以及任何 MCP 宿主）以**可枚举、可审查**的方式取数。
>
> 完整设计与取舍见 [`docs/sprint/SPRINT_10.md`](../../docs/sprint/SPRINT_10.md)。

---

## 1. 它是什么，不是什么

| 是 | 不是 |
| --- | --- |
| 一个 MCP 服务器，暴露 **4 个只读工具** | ❌ 不是数据库网关（它没有数据库凭据） |
| `services/api` 已有接口的一对一映射 | ❌ 不是第二条独立的取数实现 |
| 独立 systemd 单元（127.0.0.1:8200） | ❌ 不是对公网开放的端口 |
| streamable-http（部署）/ stdio（本地宿主）两种传输 | ❌ 不用已被取代的 SSE |

```text
路径 A（直连）  Agent ── HTTP ─────────────────────────► 只读数据服务 ──► Doris
路径 B（MCP）   Agent ── MCP(streamable-http) ──► MCP 服务 ── HTTP ──► 只读数据服务 ──► Doris
                                                              └─ 无凭据
```

**两条路径的终点、守卫、账号完全相同**，差别只在"谁来发起那次 HTTP"。
MCP 的价值不在多一跳，而在**能力面可枚举**：MCP 服务器声明的工具
就是它能做的一切，客户端可以 `list_tools()` 读出来交给安全审查。

---

## 2. 暴露的能力（全部只读，一个不多）

| MCP 工具 | 映射到的只读接口 | 用途 |
| --- | --- | --- |
| `metrics_lookup` | `GET /data/api/meta/metrics` | 查指标口径（唯一权威 `sql/metadata/metrics.md`） |
| `tables_lookup` | `GET /data/api/meta/tables` | 查表结构（**只含 `sqlguard` 授权表**） |
| `sql_query` | `POST /data/api/query` | 执行只读 SELECT（**唯一取数通道**） |
| `reconciliation` | `GET /data/api/batch/reconcile` | 查批流对账结论 |

**刻意没有**：写工具、`SHOW`/`DESCRIBE` 这类元数据探测、任意 HTTP 出口、
"列出所有表"、文件读写。理由见 `app/server.py` 的模块说明。

---

## 3. 铁律在代码里的落点（`AGENTS.md` §10）

| 铁律 | 落点 |
| --- | --- |
| 不得给 MCP 直接数据库凭据 | `app/config.py` **只读 4 个非敏感环境变量**；systemd 单元**没有** `EnvironmentFile=.env`（`.env` 是 `root:dpapi 0640`，本进程连文件都碰不到） |
| 不得绕过 `sqlguard` | `app/client.py` 唯一的取数实现是 HTTP 调 `POST /data/api/query`；**没有**任何数据库驱动依赖 |
| 权限最小化 | 4 个工具，全部只读，与只读接口一一对应 |
| 失败即失败 | `client.py` 把数据服务的错误信封原样转成结构化错误；`server.py` 里**不存在**任何兜底默认值分支 |

---

## 4. 起停与自检

```bash
# 部署（幂等；由验收脚本调用，会创建 dpmcp 用户 + 装单元 + 起服务）
bash scripts/verify-sprint-10.sh

systemctl status data-platform-mcp
journalctl -u data-platform-mcp -n 100 --no-pager
curl -s http://127.0.0.1:8200/healthz | python3 -m json.tool

# 手工以 stdio 形态跑（本地宿主用；stdout 是协议线路，日志走 stderr）
cd /opt/data-platform/services/mcp && \
  /opt/data-platform/.venv-agent/bin/python -m app.main --transport stdio
```

---

## 5. 配置项（环境变量，全部非敏感）

systemd 单元里注入；**不读 `.env`**。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MCP_DATA_API_BASE` | `http://127.0.0.1:8000` | 只读数据服务地址（唯一数据出口） |
| `MCP_HOST` | `127.0.0.1` | 监听地址（只回环；对外必须经 Nginx） |
| `MCP_PORT` | `8200` | 监听端口 |
| `MCP_HTTP_PATH` | `/mcp` | MCP 端点路径 |
| `MCP_QUERY_LIMIT` | `200` | 单次查询行数上限 |
| `MCP_API_TIMEOUT` | `30` | 单次数据服务调用超时（秒） |

Agent 侧对应配置（`services/agent`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_DATA_PATH` | `http` | `http` = 直连只读服务；`mcp` = 经 MCP 取数 |
| `AGENT_MCP_SERVER_URL` | `http://127.0.0.1:8200/mcp` | MCP 端点（streamable-http） |
| `AGENT_MCP_TRANSPORT` | `streamable-http` | `streamable-http` 或 `stdio` |
| `AGENT_MCP_STDIO_COMMAND` | `python` | stdio 形态下启动 MCP 的命令 |
| `AGENT_MCP_STDIO_ARGS` | `-m,app.main,--transport,stdio` | 逗号分隔的参数 |
| `AGENT_MCP_TIMEOUT` | `60` | MCP 调用超时（秒）；必须大于单跳超时，否则会把"链路更长"误报成"不可用" |

> ⚠️ **没有自动回退**：`AGENT_DATA_PATH=mcp` 时 MCP 不可达就**失败**，
> 不会悄悄改用直连。理由：`AGENTS.md` §10.3「失败即失败」——
> 自动降级会让"MCP 到底有没有在工作"永远无法验收（脚本全绿而一次 MCP 调用都没发生）。
> 切回直连是显式动作：改 `AGENT_DATA_PATH=http` 后重启 Agent。

---

## 6. 依赖

```
mcp==2.2.0        # 官方 Python SDK（服务器 + 客户端同一个包）
```

版本依据与为什么选 2.x 而不是最后的 1.30.0，见
[`requirements.txt`](requirements.txt) 顶部说明（含 PyPI 官方 JSON API
与服务器 `pip index versions mcp` 的实测记录）。
