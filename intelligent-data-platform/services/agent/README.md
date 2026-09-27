# Agent 配置说明（Sprint 7 → 8 → 9）

> Sprint 8 起 Agent 由 **LangGraph 图**编排（检索 → 规划 → 取数 → 校验 → 反思 → 汇总），
> Sprint 9 起回答会附带**命中的口径来源**。
> 配置方式没有变（仍然只认 `.env` 里的 `LLM_API_KEY`），本文件补充新增接口与排障项。

> 面向**运维/演示者**。代码实现见 [`services/agent/`](../../services/agent)，
> 设计文档见 [`docs/sprint/SPRINT_7.md`](../../docs/sprint/SPRINT_7.md)。
>
> 核心原则：**API Key 只进 `.env`**，不进代码、不进 Git、不进日志。

---

## 1. 需要配什么

一个键就够：服务器 `/opt/data-platform/.env` 里的 `LLM_API_KEY`。

其余项都有合理默认值（见 `.env.example` 的 Sprint 7 段），不配也能跑：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `LLM_MODEL` | `deepseek-flash` | 本项目上下文短（口径字典 + 表结构 + 一条 SQL），不需要 pro；flash 支持 Tool Calls，价格约为 pro 的 1/4.5 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `LLM_THINKING` | `false` | 思考模式默认关闭，理由见 `services/agent/app/llm.py` 模块头 |
| `AGENT_MAX_TOOL_ROUNDS` | `6` | 单次提问最多几轮工具调用，超过则强制收尾 |
| `AGENT_QUERY_LIMIT` | `200` | 单条 SQL 返回行数上限（服务端还会再收敛一次） |
| `AGENT_TOTAL_TIMEOUT` | `120` | 单次提问总超时（秒）。**必须小于 Nginx 的 180s**，否则会被网关截成 504 |

---

## 2. 申请 Key

1. 打开 <https://platform.deepseek.com/> 注册并登录；
2. 在**API Keys** 页面创建一个 Key（形如 `sk-...`，只在创建时显示一次，请立即保存）；
3. 账户需要有余额（按 token 计费）。

> **计费提示**：北京时间周一至周五 9:00–12:00、14:00–18:00 为高峰时段，
> 其余时段（含周末与法定节假日）**价格减半**。
> 批量跑验收、演示排练建议避开高峰。

---

## 3. 写入配置并重启

在服务器上执行（把 `sk-xxx` 换成你的 Key）：

```bash
ssh -i <你的密钥> root@36.151.150.140

# 写入 Key（只改这一行，不动其它配置）
sed -i 's|^LLM_API_KEY=.*|LLM_API_KEY=sk-xxx|' /opt/data-platform/.env

# 校验权限：应为 root:dpapi 0640
ls -l /opt/data-platform/.env

# 重启 Agent（数据服务不需要重启）
systemctl restart data-platform-agent

# 确认已识别
curl -s http://127.0.0.1:8100/health | python3 -m json.tool
```

**成功标志**：`data.llm.configured` 为 `true`，且 `data.status` 为 `ok`。

> 注意：`sed` 会把 Key 写进 shell 历史。
> 若在意这一点，改用 `systemctl edit` 之外的方式手工编辑：
> `nano /opt/data-platform/.env`，改完保存退出。

---

## 4. 端到端验证

```bash
# 1) 工具与提示词（不需要 Key，任何时候都可用）
curl -s http://127.0.0.1:8100/tools  | python3 -m json.tool | head -30
curl -s http://127.0.0.1:8100/prompt | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["system_prompt"][:400])'

# 2) 图长什么样（Sprint 8：节点 / 边 / 三重上界，编排可审查）
curl -s http://127.0.0.1:8100/graph | python3 -m json.tool

# 3) 检索层画像（Sprint 9：后端、语料条数、**每一路语料的真实状态**）
curl -s http://127.0.0.1:8100/retrieval | python3 -m json.tool

# 4) 单独试检索（同义词是否生效一眼可见）
curl -s --get http://127.0.0.1:8100/api/retrieve \
  --data-urlencode 'q=最近一周卖了多少钱' --data-urlencode 'k=3' \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
print("同义词扩展：", [(x["phrase"], x["terms"]) for x in d["expanded_terms"]])
for h in d["hits"]:
    print("  命中：", h["citation"], "|", h["title"])
'

# 5) 真正提问（需要 Key）
curl -s -X POST http://127.0.0.1:8100/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"最近一周每天的 GMV 是多少？"}' \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)["data"]
print("回答：", d["answer"][:600])
print("用到的表：", d["tables"])
print("实际执行的 SQL：", d["executed_sql"])
print("命中的口径来源：", [(x["citation"], x["title"]) for x in d.get("docs", [])])
print("图路径：", " -> ".join(d.get("graph", {}).get("path") or []))
print("重试次数：", d.get("retries"), " 校验通过：", d.get("validation", {}).get("ok"))
print("工具调用轮次：", d["rounds"], " 耗时：", d["elapsed_ms"], "ms")
'
```

回答里必须能看到这些字段，缺一说明链路有问题：

| 字段 | 含义 | 为什么必须有 |
| --- | --- | --- |
| `tables` | 这次读了哪些表 | 「回答必须能说明数据来源」 |
| `executed_sql` | 服务端**实际执行**的语句（守卫改写后的） | 「不得伪造查询结果」要可核对 |
| `steps` | 每一步调了什么工具、成败 | 回答不对时用来定位是哪一步错了 |
| `docs`（S8/S9） | 命中的口径/表结构/分层文档，带 `source` 与 `line` | 引用口径必须能**翻回原文**核对 |
| `plan`（S8） | 显式规划（目标表 + SQL 草稿 + 理由） | 让"它想怎么查"成为可审查的产物 |
| `validation` / `retries`（S8） | 校验结论与**已用反思重试次数** | 失败与重试都要可数，而不是黑箱 |
| `graph.path`（S8） | 这一次实际走过了哪些节点 | 一眼看出有没有触发重规划 |

浏览 `https://<服务器IP>/data/#/ask` 同样可以提问，
页面上会把上面三项直接展示出来。

> 站点用**自签证书**，浏览器首次访问需点一次「高级」→「继续前往」。
> 原因见 [`docs/sprint/SPRINT_6.md`](../../docs/sprint/SPRINT_6.md) 第 8 节。

---

## 5. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `/ask` 返回 503 `LLM_NOT_CONFIGURED` | Key 没配或仍是占位符 | 按第 3 节写入并重启 |
| `/ask` 返回 401 / 402 | Key 无效 / 余额不足 | 到 DeepSeek 平台核对 |
| `/ask` 返回 400 且提到 `reasoning_content` | 开了思考模式但历史轮次没回传 | 本仓库的实现已统一负责回传；若自行改过 `llm.py` 请对照官方文档 |
| `/ask` 返回 400 且提到 `must be followed by tool messages` | 规划用的 `propose_sql` 工具调用没有被回应 | Sprint 8 已修为自动补一条"计划已受理"的 tool 消息；若改过 `graph.py` 的 `node_plan` 请保持该行为 |
| 提问很慢最后 504 | `AGENT_TOTAL_TIMEOUT` 大于 Nginx 超时 | 保持 `AGENT_TOTAL_TIMEOUT < 180` |
| 回答正确但总说"查不到" | 表名没写库名，或该表根本不在白名单 | 提示词已要求写 `库.表`；若是新表，需同时加进 `sqlguard.BUSINESS_TABLES`（注意：**表存在 ≠ 已授权**，`ads_traffic_1d` 就是典型） |
| 回答里不再引用口径（没有 `docs`） | 检索层没装载（语料路径不对 / 接口不通） | `curl -s http://127.0.0.1:8100/retrieval` 看 `channels`，哪一路失败会写明原因 |
| 同一个错反复重试、回答像"重放" | 规划器上下文里混进了上一轮的查询结果 | Sprint 8 已把规划上下文收窄为"问题 + 检索结果 + 上一版计划 + 真实失败"；不要改回复用全局消息流 |
| Agent 报无法连接数据服务 | `data-platform-api` 未启动 | `systemctl status data-platform-api` |
| **Agent 起不来，日志是 `PermissionError: '/opt/data-platform/.env'`** | `.env` 权限被收紧成 `600`，而服务以 `dpagent` 运行、需要**组读** | `chown root:dpapi /opt/data-platform/.env && chmod 640 /opt/data-platform/.env && systemctl restart data-platform-agent`（注意 `scripts/deploy-monitoring.sh` 会把它改回 600） |
| 图单元测试报"缺少 langgraph 依赖" | 用了数据服务的 `.venv`（langgraph 只装在 `.venv-agent`） | 用 `/opt/data-platform/.venv-agent/bin/python -m pytest …` |

```bash
# 看日志（Agent 的所有 LLM 调用与工具调用都会留下痕迹）
journalctl -u data-platform-agent -n 100 --no-pager
journalctl -u data-platform-agent -f
```

---

## 6. 安全边界（不要绕过）

```text
Agent 进程里没有数据库凭据 —— 它只能通过 HTTP 调只读数据服务。
数据服务侧：SQL 守卫（仅 SELECT / 表白名单 / 强制 LIMIT）+ 只读账号 agent_ro。
写操作会被**两层**拒绝：守卫直接拒绝，即使绕过守卫 Doris 也会拒绝。
```

因此：

- **不要**为了"让它能查更多表"而放宽 `sqlguard`，应先确认该表是否应该被暴露；
- **不要**把 `API_DORIS_USER` 改成 `root`；
- **不要**把 Key 写进代码、文档或提交到 Git（`.env` 已在 `.gitignore` 中）。

新增可查表的标准做法：把表名加进
`services/api/app/sqlguard.py` 的 `BUSINESS_TABLES`，并在
`services/agent/app/agent.py` 的系统提示词「数据地图」里补上表名与字段 ——
两处都要改，否则模型知道有这张表却不知道有哪些列。

> **Sprint 9 起还有第三处（自动的）**：表结构语料经 `GET /meta/tables` 装载，
> 而该接口就是按 `BUSINESS_TABLES` 取数的，所以**加了白名单，检索侧会自动跟上**
> （最多 10 分钟 TTL，或重启 Agent 立即生效）。
> 这也意味着"表存在但不在白名单"时，Agent 查得到它的元数据却查不到数据 ——
> 此时它会如实说明"未授权"，而不是假装没有这张表（实测行为）。

Agent 侧新增能力（Sprint 8/9，都不扩大取数权限）：

```text
propose_sql    图的规划协议：产出结构化计划，**不执行任何查询**
retrieve_docs  在口径/表结构/分层文档里做词法检索（读仓库内文档，不碰数据库）
```

因此**能取业务数据的工具仍然只有 `sql_query` 一个**
（`verify-sprint-8.sh` 第 4 步会断言这一点）。
