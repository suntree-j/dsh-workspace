# Sprint 10 — MCP（Model Context Protocol）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 依据：[`docs/PROJECT_DESIGN_V1.md`](../PROJECT_DESIGN_V1.md) 第 7 章（Agent 架构）、
> 第 8 章 Roadmap 第 10 行；[`AGENTS.md`](../../AGENTS.md) §10（Agent 安全规范）
> 前置：Sprint 7（Agent 主体）、Sprint 8（LangGraph 图）、Sprint 9（检索增强）
> 状态：✅ **已完成并验收通过**（`verify-sprint-10.sh` 84/0/0；详见第 9 节「实施记录」）

---

## 1. 目标

把**只读数据服务**的能力用 MCP 暴露出来，Agent 可经 MCP 取数。

Sprint 7 定下了一条硬规范：

> **Agent 进程里不允许有数据库凭据。** 它只能经 HTTP 调只读数据服务取数。

Sprint 10 要在这个前提下回答一个新问题：

```text
"Agent 能做什么" 这件事，能不能不被藏在 Agent 的源码里，而是被**声明**出来？
```

MCP 给出的答案是一个**能力清单**：服务器声明它提供哪些工具，客户端可以
`list_tools()` 把它读出来。这让"权限最小化"从一句设计口号变成**可枚举、可审查**的事实。

---

## 2. 两条路径：MCP 不是第二条取数实现

```text
路径 A（直连）  Agent ── HTTP ─────────────────────────► 只读数据服务 ──► Doris
路径 B（MCP）   Agent ── MCP(streamable-http) ──► MCP 服务 ── HTTP ──► 只读数据服务 ──► Doris
                                                              └─ 无凭据
```

**两条路径的终点、守卫、账号完全相同**（`POST /data/api/query` + `sqlguard` + `agent_ro`），
差别只在"谁来发起那次 HTTP"。

> 这一点是本 Sprint 的设计核心：**MCP 不是一条新的取数通道，而是同一份只读能力
> 的另一种暴露方式。** 如果它变成"另一条能取数的路"，那"不得绕过 sqlguard"
> 这条铁律立刻就被绕过了 —— 因为绕过的方式恰恰是"再实现一遍"。

---

## 3. 铁律在代码里的落点（`AGENTS.md` §10）

| 铁律 | 落点 | 怎么证明 |
| --- | --- | --- |
| **不得给 MCP 直接数据库凭据** | `services/mcp/app/config.py` 只读非敏感环境变量；systemd 单元**没有** `EnvironmentFile=.env`（`.env` 是 `root:dpapi 0640`） | 验收第 3 步：扫单元文件 + 扫 `/proc/<pid>/environ` + 扫源码，都不许出现任何口令键名 |
| **不得绕过 `sqlguard`** | `services/mcp/app/client.py` 唯一的取数实现是 HTTP 调 `POST /data/api/query`；MCP 的依赖里**没有**任何数据库驱动 | 验收第 4 步：经 MCP 发 DELETE / 未授权表 / 多语句 / 注释注入，四类都必须被**拒绝**且原因正确 |
| **权限最小化** | 暴露 4 个只读工具，与只读接口一一对应；没有写工具、没有任意 HTTP 出口、没有文件读写 | 验收第 2 步：工具数恰为 4，且与 Agent 的取数工具**逐名相等** |
| **失败即失败** | `client.py` 把数据服务的错误信封原样抛出；`server.py` 里不存在任何兜底默认值分支 | 验收第 4 步的拒绝原因；单元测试 `test_mcp_failure_does_not_fall_back_to_direct_http` |

---

## 4. 版本：`mcp==2.2.0`（查证后钉住）

**禁止 `latest`、禁止猜。** 查证过程与结论：

| 证据 | 内容 |
| --- | --- |
| PyPI 官方 JSON API（`https://pypi.org/pypi/mcp/json`） | `info.version = 2.2.0`；官方 README 写明 2.x 是 *"the current stable release line"*，1.x 只收关键修复 |
| 服务器实测 `pip index versions mcp` | `2.2.0, 2.1.1, 2.1.0, 2.0.1, 2.0.0, 1.30.0, 1.29.1, …` |
| 实测安装 | `Successfully installed … mcp-2.2.0 mcp-types-2.2.0 …`；`pip show mcp` → `Version: 2.2.0` |

### 4.1 为什么选 2.2.0 而不是最后的 1.x（1.30.0）

1. 2.x 是官方当前稳定线，1.x 只收关键修复与安全补丁；
2. v2 支持 2026-07-28 版 MCP 规范（含此前全部修订），1.x 不支持新规范；
3. 本项目的用法（`MCPServer` + `@server.tool()` + `Client(url)`）在 v2 里是最短路径，
   **没有用到任何被弃用的 API**。安装后实测确认 `MCPServer` / `Client` /
   `streamable_http_app` / `custom_route` 全部存在且签名符合预期。

> ⚠️ **v1 → v2 是破坏性重构**（`FastMCP` → `MCPServer`、`ClientSession` → `Client`、
> `httpx` → `httpx2`）。这正是必须**钉死版本**而不是给区间的原因：
> 给区间等于允许在一次部署里换掉整套 API 形状。

### 4.2 传递依赖与"不新增技术栈"的核对

实测带入 13 个包：`mcp-types` / `pydantic` / `starlette` / `uvicorn` / `anyio` /
`httpx2` / `jsonschema` / `pyjwt[crypto]` / `cryptography` / `cffi` / `pycparser` /
`opentelemetry-api` / `sse-starlette` / `attrs` / `referencing` / `rpds-py` /
`python-multipart`。

其中 **`starlette` 与 `uvicorn` 项目里本来就有**（数据服务用的是 FastAPI 那一套），
所以**没有引入新的技术栈类型**（`AGENTS.md` §2.3）：只是把同一门 HTTP/ASGI
技术多接了一个官方 SDK。`services/mcp/requirements.txt` 里写清了这一条。

---

## 5. 架构与传输选择

```text
services/mcp/
  app/
    config.py       ← 配置（**只读非敏感环境变量**，不碰 .env）
    client.py       ← 唯一数据出口：HTTP 调只读数据服务
    server.py       ← MCP 服务器（4 个只读工具 + /healthz）
    main.py         ← 入口（--transport streamable-http|stdio）
  deploy/
    data-platform-mcp.service   ← systemd 单元（dpmcp 用户、回环监听、不读 .env）
  tools/
    mcp_probe.py    ← 验收探针（官方 SDK 的真实 MCP 客户端）
  requirements.txt  ← mcp==2.2.0（钉死）
```

### 5.1 为什么**两种传输都做**，但以 streamable-http 为部署形态

| 维度 | streamable-http（**部署形态**） | stdio（本地形态） |
| --- | --- | --- |
| 生命周期 | 独立 systemd 单元常驻，与请求解耦 | 宿主把服务拉成子进程，生命周期绑在调用方 |
| 可运维性 | `systemctl status` / `journalctl`，与项目里其它服务同一套手法 | 排障时"服务没起来"与"调用写错了"症状相同 |
| 故障影响 | 崩了只影响 MCP，重启即可 | 崩了直接影响那一次调用 |
| 复用 | 一个进程可被多个客户端共用 | 一个宿主一个进程 |
| stdout 风险 | 无（走 HTTP） | **stdout 就是协议线路**，一行杂散输出就破坏数据流 |

选 streamable-http 作为**部署形态**的理由是可运维性；
stdio 保留成 `--transport stdio` 是因为桌面宿主（Claude Desktop 等）与
`mcp dev` 检查器按约定就是 stdio，留一个开关比事后再加便宜。

**两者暴露的是同一份工具实现**（`server.build_server()`），
所以"换个传输就换了能力面"这种事不会发生 —— 这一条由单元测试
`test_mcp_exposes_exactly_the_four_readonly_tools` 与源码扫描共同锁住。

> **不用 SSE**：SSE 在 2025-03-26 协议修订版中已被 Streamable HTTP 取代，
> 官方文档明确写着 "不要用"。

### 5.2 Agent 侧客户端接入路径（`services/agent/app/mcp_client.py`）

```text
AGENT_DATA_PATH=http   工具直连只读数据服务（默认，与 Sprint 7~9 完全相同）
AGENT_DATA_PATH=mcp    工具经 MCP 服务取数
```

**为什么是显式开关，不是"能连 MCP 就用 MCP"**：

> 验收必须能回答"这一次到底走了哪条路"。自动降级会让 MCP 路径在任何一次
> 连接抖动后**悄悄消失**，而所有断言仍然全绿 —— 这是最难发现的一类失效。

**为什么同步门面 + 后台事件循环**：官方 `Client` 是异步的，而工具层是同步的。
三种桥接方式的取舍写在 `mcp_client.py` 的模块说明里，结论是"后台守护线程持有
长连接"：MCP 会话本身就是长连接（streamable-http 也是围绕会话与事件流设计的），
每次调用重建连接会丢掉会话并反复握手。

**失败即失败**：MCP 不可达时工具返回失败结果，**不**自动回退到直连。
理由同上 —— 静默回退会让"MCP 有没有在工作"无法验收。

### 5.3 两条路径的结果形状必须一致

MCP 的 `tables_lookup` 返回 `{"tables": [...]}`，而直连 `/meta/tables` 返回 rows；
MCP 的 `reconciliation` 返回 `latest_batch`，而直连返回 `latest`。

**在工具层一次抹平**（`ToolBox._call_via_mcp`）。若不复原，
图（Sprint 8）里读 `result.tables` 与 `_extract_number(content, "row_count")`
的地方会**静默拿到空值** —— 表现为"回答里没有数字"，而工具其实成功了。

---

## 6. 阶段划分

| 阶段 | 内容 | 完成判据 |
| --- | --- | --- |
| 1 | 写本任务书 | 本文件存在 |
| 2 | 查证并钉住 `mcp` 版本，装成功 | `pip show mcp` → `2.2.0`；写进 `docs/development-environment.md` |
| 3 | MCP 服务 `services/mcp/` | `list_tools()` 返回 4 个只读工具 |
| 4 | 部署（systemd + 专用用户） | `systemctl is-active data-platform-mcp` = active |
| 5 | Agent 客户端路径 | `AGENT_DATA_PATH=mcp` 时工具确实经 MCP |
| 6 | `tests/test_mcp.py` | `pytest tests/test_mcp.py -q` 全绿 |
| 7 | `scripts/verify-sprint-10.sh` | 分步 `[OK]/[FAIL]`、统计、失败 exit 1 |
| 8 | **端到端实证** | 同一 SQL 经 MCP 与经直接 HTTP 的结果**逐字段一致** |
| 9 | 回归 | `verify-sprint-7.sh` 49/49；Sprint 8 / 9 单测不回退 |
| 10 | 文档 | 本文件第 9 节 + `DEVELOPMENT_LOG.md` + `README.md` |

---

## 7. Definition of Done

| # | 判据 | 判定方式（必须有实证） |
| --- | --- | --- |
| 1 | MCP 版本查证并钉住 | `pip show mcp` 的 `Version` == `requirements.txt` 的 `==` 值 |
| 2 | 能力面最小且可枚举 | `list_tools()` 恰好 4 个只读工具，且与 Agent 取数工具**逐名相等** |
| 3 | MCP 无数据库凭据 | 单元无 `EnvironmentFile`；`/proc/<pid>/environ` 无任何口令键；源码无驱动 |
| 4 | 不绕过 sqlguard | 经 MCP 的 DELETE / 未授权表 / 多语句 / 注释注入四类**全部被拒且原因正确** |
| 5 | **两条路径结果一致** | 同一 SQL 的 `rows` JSON 规范化后 `IDENTICAL`；`executed_sql` 也相同 |
| 6 | 非空守卫 | 一致性比对之前先证明两边都真的取到了行（避免"两边都空"的假通过） |
| 7 | 离线可测 | `pytest tests/test_mcp.py -q` 全绿，全部 `pytest.mark.unit`，零外部依赖 |
| 8 | 回归 | `verify-sprint-7.sh` 49/49 不回退；Sprint 8 / 9 单测不回退 |
| 9 | 端口纪律 | MCP 只监听 `127.0.0.1:8200`，不进 `docker-compose.yml`，不改 Nginx |

---

## 8. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| MCP 变成"第二条取数实现"，守卫被旁路 | 安全底线失效 | 唯一的取数实现是 HTTP 调 `POST /query`；验收用四类攻击正面打 |
| 两条路径形状不同 | 图静默拿到空值（"回答里没有数字"） | 工具层一次抹平 + 单元测试逐字段断言 |
| 自动回退掩盖 MCP 失效 | 验收全绿而一次 MCP 调用都没发生 | **不做**自动回退；路径由 `AGENT_DATA_PATH` 显式决定 |
| v1 → v2 破坏性变更 | 一次部署换掉整套 API 形状 | 钉死 `==2.2.0`；服务端与客户端必须同一个版本（单测断言） |
| 表名提取规则两处实现漂移 | 血缘信息与实际查询不一致 | Agent 侧 `extract_tables` 与 `sqlguard.extract_tables` 逐例比对（单测） |
| MCP 端口对外暴露 | 未授权访问 | 单元只绑 `127.0.0.1`；不进 compose、不改 Nginx；验收断言 `MCP_HOST=127.0.0.1` |

---

## 9. 实施记录

### 9.1 落地的文件

| 文件 | 变更 |
| --- | --- |
| `services/mcp/app/config.py` | **新增**：配置（只读非敏感环境变量，**不碰 `.env`**） |
| `services/mcp/app/client.py` | **新增**：唯一数据出口 —— HTTP 调只读数据服务 |
| `services/mcp/app/server.py` | **新增**：MCP 服务器（4 个只读工具 + `/healthz`），两种传输共用 |
| `services/mcp/app/main.py` | **新增**：入口（`--transport streamable-http\|stdio`） |
| `services/mcp/deploy/data-platform-mcp.service` | **新增**：systemd 单元（`dpmcp` 用户、回环、不读 `.env`） |
| `services/mcp/tools/mcp_probe.py` | **新增**：验收探针（官方 SDK 的真实 MCP 客户端） |
| `services/mcp/requirements.txt` | **新增**：`mcp==2.2.0`（含版本查证依据） |
| `services/mcp/README.md` | **新增**：使用与配置说明 |
| `services/agent/app/mcp_client.py` | **新增**：Agent 侧 MCP 客户端（后台事件循环 + 长连接） |
| `services/agent/app/config.py` | 新增 `AGENT_DATA_PATH` / `AGENT_MCP_*` 与 `_env_choice`（非法值启动即失败） |
| `services/agent/app/tools.py` | 新增取数路径分流（`ToolBox.call` 单一分流点）+ `extract_tables` |
| `services/agent/app/main.py` | 新增 `GET /mcp`（现场列出对端声明的工具）；`/health` 增 `data_path` |
| `services/agent/requirements.txt` | 新增 `mcp==2.2.0`（与服务端同版本） |
| `tests/test_mcp.py` | **新增**：MCP 单元测试（能力面 / 凭据 / 分流 / 形状 / 解包） |
| `scripts/verify-sprint-10.sh` | **新增**：6 步验收（含幂等部署） |

### 9.2 关键设计取舍（3 条，值得单独记）

#### 9.2.1 分流点只有一个，直连实现一行没改

`ToolBox.call()` 是**唯一**的取数路径分流点：

```python
if self.data_path == "mcp" and name in _MCP_TOOL_NAMES:
    return self._call_via_mcp(name, arguments)
```

放在这一处而不是散在五个方法内部，有两个实际好处：
"哪些工具走了 MCP"是一眼可读的事实；而 Sprint 7 已验收的直连实现
（`_get` / `_post`）**一行都不用改**，回归风险最小。

#### 9.2.2 非取数工具**不**走 MCP

`retrieve_docs` 查的是 Agent 自己的语料文件，`propose_sql` 是图内部的规划协议。
把它们也塞进 MCP 会让"最小能力集合"这个说法失真 —— 它们本来就不是取数能力。
单元测试 `test_non_fetch_tools_never_go_through_mcp` 锁住这一点。

#### 9.2.3 `tables_lookup` 的"查不到"报失败，而"没有可用表"报成功

`tables_lookup(table=不存在)` 返回 `ok=False` 并列出可用表 ——
因为对模型来说"表不存在"与"表存在但没有列"是完全不同的信息，
后者会让它以为"这张表是空的"从而写出一条**语法正确但语义错误**的 SQL。

### 9.3 一处必须说清的边界（不是缺陷）

`sqlguard.METADATA_TABLES` 里包含 `information_schema.tables` / `.columns`
（Sprint 9 的 9.6 节记录过）。MCP 的 `tables_lookup` 走的是
`GET /meta/tables`（服务自己拼的元数据查询），**不**接受用户传入的表名，
因此经 MCP 无法做"任意元数据探测" —— 能力面比直连 `/query` 更窄。

本 Sprint **没有**改动 `sqlguard` 的授权集合：那是数据服务侧的权限边界，
改动它属于另一个决定。此处仅记录，供后续 Sprint 复核。

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| — | V1.0 | 建立 Sprint 10 任务书（实现前先行） |
| 2026-09-28 | V1.1 | 回填实施记录：`mcp==2.2.0`（PyPI + 服务器双重查证）；6 步验收 |
