"""数据问答 Agent（Sprint 7 → S8 图式编排 → S9 检索增强 → S10 MCP 取数路径）。

定位：
    这是「智能层」的可运行形态 —— 自然语言问题进，带来源说明的回答出。
    它**不直连数据库**：所有取数都经 `services/api` 的只读接口
    （Sprint 10 起也可以经 MCP 服务，见下），
    因此 Agent 的能力边界与一个只读用户完全一致（进程边界即权限边界）。

启动：
    uvicorn app.main:app --host 127.0.0.1 --port 8100
    （生产由 systemd 托管，见 deploy/systemd/data-platform-agent.service）

与数据服务的关系：
    Nginx 把 /data/agent/ 反代到本服务，/data/api/ 反代到数据服务。
    对外只有一个入口，看板通过同源路径调用，不需要额外开端口。

## `/ask` 走哪条路（Sprint 8 起）

默认走 **LangGraph 图式 Agent**（`app/graph.py`）：
`retrieve → plan → execute → validate →（reflect → plan）→ summarize`。

`AGENT_GRAPH_ENABLED=false` 时回退到 Sprint 7 的单轮工具调用循环（`app/agent.py`）。
保留这条回退路径的原因很实际：**排障与回归要有基线** ——
图出问题时，能立刻判断"是图的问题还是数据/模型的问题"。
它不是长期的双实现，`/ask` 的响应里 `engine` 字段会如实说明用了哪条。

## 取数走哪条路（Sprint 10）

`AGENT_DATA_PATH=http`（默认）→ 工具直接打只读数据服务；
`AGENT_DATA_PATH=mcp`      → 工具经 MCP 服务取数（`app/mcp_client.py`）。

两条路径的终点与守卫完全相同，`GET /mcp` 会如实报告当前走的是哪条，
并把它**从 MCP 服务现场发现的工具清单**列出来（能力面可审查）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .agent import SYSTEM_PROMPT, Agent, describe_tools
from .config import ConfigError, Settings, load_settings
from .retrieval import build_retriever
from .timeutil import now_iso

SETTINGS: Settings
CONFIG_ERROR: str = ""
try:
    SETTINGS = load_settings()
except ConfigError as exc:  # 配置非法：启动即失败，不留"半可用"状态
    raise SystemExit(f"Agent 配置错误：{exc}") from exc

app = FastAPI(
    title=SETTINGS.title,
    version=SETTINGS.version,
    description=(
        "批流一体智能数据分析平台的数据问答 Agent。\n\n"
        "自然语言 → 元数据/口径检索 → 规划 → SQL 生成 → 安全检查 → 只读执行 "
        "→ 结果校验 → 反思重试 → 带来源的回答。\n"
        "编排为 LangGraph 图（Sprint 8），检索为词法后端（Sprint 9，无向量库）。\n"
        "所有取数都经只读数据服务，Agent 自身没有数据库凭据。"
    ),
    root_path="/data/agent",  # 经 Nginx 反代后的对外路径（保证 /docs 链接正确）
)

# 检索器（进程内单例）：语料按文件 mtime 与表结构 TTL 缓存，
# 改了口径文档不必重启服务。
_RETRIEVER = build_retriever(SETTINGS)


# ------------------------------------------------------------
# 请求模型
# ------------------------------------------------------------
class AskRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="自然语言问题，例如「最近一周每天的 GMV 是多少？」",
    )


# ------------------------------------------------------------
# 异常处理
# ------------------------------------------------------------
@app.exception_handler(ValueError)
async def _handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "处理失败", "detail": str(exc)}},
    )


# ------------------------------------------------------------
# 接口
# ------------------------------------------------------------
@app.get("/", summary="接口索引")
def index() -> dict[str, Any]:
    return {
        "data": {
            "service": "数据问答 Agent",
            "version": SETTINGS.version,
            "engine": _engine_name(),
            "endpoints": [
                "/health", "/tools", "POST /ask", "/prompt",
                "/graph", "/retrieval", "/api/retrieve", "/mcp",
            ],
            "docs": "/docs",
        },
        "source": {
            "tables": [],
            "metric_definitions": [],
            "time_range": {"start": None, "end": None},
            "note": "Agent 只经只读数据服务取数，自身没有数据库凭据",
        },
        "generated_at": now_iso(),
    }


def _engine_name() -> str:
    return "langgraph" if SETTINGS.graph_enabled else "single-loop（Sprint 7 回退）"


def _data_path_profile(probe_mcp: bool = True) -> dict[str, Any]:
    """报告"取数走哪条路"，并（可选地）现场探测 MCP 服务能提供什么。

    `probe_mcp=False` 用于 `/health`：健康检查会被监控高频调用，
    不该每次都去建一条 MCP 会话。`GET /mcp` 才做真探测 ——
    它是"能力面可审查"的接口，慢一点是对的。
    """
    profile: dict[str, Any] = {
        "path": SETTINGS.data_path,
        "transport": SETTINGS.mcp_transport,
        "server_url": SETTINGS.mcp_server_url,
        "timeout_s": SETTINGS.mcp_timeout,
        # 这一条**无条件**出现：它是设计声明，不是"只在某条路径下才成立"的属性。
        # 曾经只在 mcp 路径下给 note，结果 http 路径下 /mcp 不解释"为什么不回退" ——
        # 而"不自动回退"恰恰是最需要被读到的一条约定。
        "no_silent_fallback": True,
        "note": (
            "http = 工具直连只读数据服务；mcp = 工具经 MCP 服务取数。"
            "两条路径的终点与守卫（sqlguard + 只读账号）完全相同；"
            "**不会自动回退**（MCP 不可达即失败）—— 走哪条路由本配置显式决定。"
        ),
    }
    if SETTINGS.data_path != "mcp":
        profile["reachable"] = None
        profile["detail"] = "当前取数路径是 http，未连接 MCP"
        return profile

    if not probe_mcp:
        return profile

    from .mcp_client import McpToolClient

    client = McpToolClient(SETTINGS)
    try:
        tools = client.list_tools()
        profile["reachable"] = True
        profile["detail"] = f"MCP 服务可用，声明了 {len(tools)} 个工具"
        profile["remote_tools"] = tools
    except Exception as exc:  # noqa: BLE001 - MCP 不可达必须在接口上可见
        profile["reachable"] = False
        profile["detail"] = f"{type(exc).__name__}: {exc}"
        profile["remote_tools"] = []
    return profile


@app.get("/health", summary="健康检查")
def health() -> dict[str, Any]:
    """报告 Agent、数据服务、LLM 与检索层的可用性。

    `llm.configured=false` 不是故障：服务可以正常启动与自检，
    只是 `/ask` 会明确拒绝并给出配置步骤 —— 这比"启动就失败"
    更便于部署（可以先装服务、再申请 Key）。

    !! 为什么也用统一信封 !!
        看板的 `runRequest` 对所有接口都按信封解析（必须有 data 字段）。
        返回裸对象会让前端报"接口返回格式不符合约定"，而服务其实完全正常 ——
        属于自找的联调成本。项目内所有接口保持同一种响应形状，
        前端才能用一套代码处理。
    """
    import urllib.error
    import urllib.request

    api_ok = False
    api_detail = ""
    try:
        with urllib.request.urlopen(f"{SETTINGS.data_api_base}/health", timeout=5) as resp:
            api_ok = resp.status == 200
            api_detail = f"HTTP {resp.status}"
    except (urllib.error.URLError, OSError) as exc:
        api_detail = f"{type(exc).__name__}: {exc}"

    # 检索层状态：语料是否真的装载了。装不上时 /ask 仍可用，
    # 但必须在这里可见 —— 否则"回答里不再引用口径"会被误认为模型变笨。
    retrieval_detail: dict[str, Any] = {"ok": False, "detail": "未装载"}
    try:
        profile = _RETRIEVER.describe()
        retrieval_detail = {
            "ok": profile["docs_total"] > 0,
            "enabled": SETTINGS.retrieval_enabled,
            "backend": profile["backend"],
            "docs_total": profile["docs_total"],
            "counts_by_kind": profile["counts_by_kind"],
            "channels": profile["channels"],
        }
    except Exception as exc:  # noqa: BLE001 - 检索层故障必须可见
        retrieval_detail = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    status = "ok" if (api_ok and SETTINGS.llm_configured) else "degraded"
    return {
        "data": {
            "status": status,
            "engine": _engine_name(),
            "data_api": {"ok": api_ok, "base": SETTINGS.data_api_base, "detail": api_detail},
            "data_path": _data_path_profile(probe_mcp=False),
            "llm": {
                "configured": SETTINGS.llm_configured,
                "provider": SETTINGS.llm_provider,
                "model": SETTINGS.llm_model,
                "base_url": SETTINGS.llm_base_url,
                "thinking_mode": SETTINGS.llm_thinking,
            },
            "retrieval": retrieval_detail,
            "limits": {
                "max_tool_rounds": SETTINGS.max_tool_rounds,
                "max_retries": SETTINGS.max_retries,
                "query_limit": SETTINGS.query_limit,
                "total_timeout": SETTINGS.total_timeout,
                "retrieval_top_k": SETTINGS.retrieval_top_k,
                "mcp_timeout": SETTINGS.mcp_timeout,
            },
        },
        "source": {
            "tables": [],
            "metric_definitions": [],
            "time_range": {"start": None, "end": None},
            "note": "Agent 只经只读数据服务取数，自身没有数据库凭据",
        },
        "generated_at": now_iso(),
    }


@app.get("/tools", summary="可用工具清单")
def tools() -> dict[str, Any]:
    """Agent 能做什么 —— 用工具声明回答，而不是用自然语言描述。

    这样"Agent 的能力"是**可枚举、可审查**的：
    安全审查只需要看这几个工具的声明与实现，不需要读提示词去猜。
    """
    return {
        "data": {
            "tools": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                }
                for spec in describe_tools()
            ],
        },
        "source": {
            "tables": [],
            "metric_definitions": [],
            "time_range": {"start": None, "end": None},
            "note": "所有取数都经只读数据服务；Agent 没有数据库凭据，也没有写权限",
        },
        "generated_at": now_iso(),
    }


@app.get("/prompt", summary="系统提示词")
def prompt() -> dict[str, Any]:
    """返回系统提示词原文。

    让提示词**可审计**：它是 Agent 行为的总纲，
    把它藏起来会让"Agent 为什么这么答"变成一件只能猜的事。
    """
    return {
        "data": {"system_prompt": SYSTEM_PROMPT, "chars": len(SYSTEM_PROMPT)},
        "source": {
            "tables": [],
            "metric_definitions": [],
            "time_range": {"start": None, "end": None},
            "note": "系统提示词原文，供审查 Agent 的行为边界",
        },
        "generated_at": now_iso(),
    }


@app.get("/graph", summary="图式编排（节点与边）")
def graph_description() -> dict[str, Any]:
    """返回 LangGraph 图的节点、边与上界。

    为什么要把"编排长什么样"做成接口：
        Sprint 8 的全部价值就是"Agent 的流程是**声明式、可审查**的"。
        若只能靠读源码才知道它经过哪几步、什么时候会重试，
        那和 Sprint 7 的隐式循环没有本质区别。
    """
    from .graph import GraphAgent

    data = GraphAgent(SETTINGS).describe()
    return {
        "data": data,
        "source": {
            "tables": [],
            "metric_definitions": [],
            "time_range": {"start": None, "end": None},
            "note": "编排为声明式图；节点/边/上界均在此列出，便于审查与论文引用",
        },
        "generated_at": now_iso(),
    }


@app.get("/mcp", summary="MCP 取数路径（Sprint 10）")
def mcp_profile() -> dict[str, Any]:
    """报告 Agent 的 MCP 接入状态，并列出 **MCP 服务现场声明的工具**。

    ## 为什么这个接口值得存在

        「能力最小化」（`AGENTS.md` §10.3 第 6 条）只有在**能力面可枚举**时
        才是可验收的。`/tools` 列出的是 Agent 交给模型的工具声明（Agent 自称），
        而这里列出的是**对端 MCP 服务实际声明的东西**（现场拉到）——
        两者都可以读，`remote_tools` 与 `/tools` 在取数类工具上一一对应。

        若 MCP 服务临时不可达，本接口**不报 500**：如实返回
        `reachable=false` 与失败原因。理由是"能力面审查"这件事不该因为
        对端抖动就失去可读性 —— 配置了哪条路径、对端地址是什么，仍然是真的。
    """
    profile = _data_path_profile(probe_mcp=True)
    return {
        "data": profile,
        "source": {
            "tables": [],
            "metric_definitions": [],
            "time_range": {"start": None, "end": None},
            "note": (
                "MCP 只暴露只读能力（4 个工具：metrics_lookup / tables_lookup / "
                "sql_query / reconciliation），且自身不持有数据库凭据；"
                "唯一取数通道仍是 POST /data/api/query，受 sqlguard 与只读账号约束。"
            ),
        },
        "generated_at": now_iso(),
    }


@app.get("/retrieval", summary="检索层画像（语料与后端）")
def retrieval_profile() -> dict[str, Any]:
    """报告检索后端、语料条数与**每一路语料的真实装载状态**。

    `channels` 是刻意暴露的：若表结构那一路取不到（接口不可达），
    检索仍可用但召回会变差。把它如实显示出来，
    比让人从"回答质量下降"去反推要可靠得多。
    """
    profile = _RETRIEVER.describe()
    return {
        "data": profile,
        "source": {
            "tables": [],
            "metric_definitions": [],
            "time_range": {"start": None, "end": None},
            "note": (
                "检索为词法后端（BM25 + 显式同义词表），**未引入向量库**；"
                "依据 docs/DECISIONS.md ⏳7。口径唯一权威是 sql/metadata/metrics.md。"
            ),
        },
        "generated_at": now_iso(),
    }


@app.get("/api/retrieve", summary="检索口径与元数据（可解释）")
def retrieve(
    q: str = Query(..., min_length=1, max_length=200, description="检索词（可用用户原话）"),
    k: int = Query(5, ge=1, le=20, description="返回条数"),
) -> dict[str, Any]:
    """对外暴露检索本身，返回命中条目、来源与**生效的同义词**。

    为什么单独开这个接口（而不是只让 `/ask` 内部用）：
        检索质量是本 Sprint 的核心可验收项，而"从回答里反推检索好不好"
        既慢又不可靠。把它开成接口后：

        - 同义词是否生效**可断言**（`expanded_terms`）；
        - 命中来源与行号**可核对**（`source` / `line`）；
        - 语料是否缺了一路**可见**（`channels` / `degraded`）。
    """
    result = _RETRIEVER.retrieve(q, top_k=k)
    return {
        "data": result.as_dict(include_text=True),
        "source": {
            "tables": [],
            "metric_definitions": [hit.doc.doc_id for hit in result.hits if hit.doc.kind == "metric"],
            "time_range": {"start": None, "end": None},
            "note": (
                f"词法检索（{result.backend}）：BM25 + 同义词扩展；"
                "命中的 source/line 可点回原文核对。"
            ),
        },
        "generated_at": now_iso(),
    }


@app.post("/ask", summary="提问（自然语言 → 带来源的回答）")
def ask(payload: AskRequest) -> dict[str, Any]:
    """回答一个自然语言问题（默认走 LangGraph 图）。

    响应里除了 `answer`，还带上：
      - `docs`          命中的口径/表结构文档（含来源文件与行号）
      - `tables`        用了哪些表（来源可追溯）
      - `executed_sql`  实际执行的 SQL（服务端返回的改写后语句，可核对）
      - `steps`         每一步调了什么工具、成功与否（过程可审计）
      - `plan`          显式规划（Sprint 8）
      - `validation`    结果校验结论与 `retries` 重试次数（Sprint 8）

    这些就是 `AGENTS.md` 第 10.1 节「最终回答必须能够说明数据来源」
    与「不得伪造查询结果」的落地形态：不是靠 Agent 自称，
    而是把可核对的东西一并返回。
    """
    if not SETTINGS.llm_configured:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "LLM_NOT_CONFIGURED",
                    "message": "LLM 未配置，无法回答问题",
                    "detail": (
                        "请在服务器 /opt/data-platform/.env 中设置 LLM_API_KEY"
                        "（DeepSeek 平台申请），然后 systemctl restart data-platform-agent。"
                        "参考 .env.example 的 Sprint 7 段。"
                    ),
                }
            },
        )

    if SETTINGS.graph_enabled:
        from .graph import GraphAgent

        result = GraphAgent(SETTINGS).ask(payload.question)
        data = result.as_dict()
        data["engine"] = "langgraph"
        docs = result.docs
        tables = result.tables
    else:
        # 回退路径：Sprint 7 的单轮工具调用循环（排障与回归用）
        legacy = Agent(SETTINGS).ask(payload.question)
        data = legacy.as_dict()
        data["engine"] = "single-loop"
        data["docs"] = []
        docs = []
        tables = legacy.tables

    return {
        "data": data,
        "source": {
            "tables": tables,
            "metric_definitions": [doc["doc_id"] for doc in docs if doc.get("kind") == "metric"],
            "documents": docs,
            "time_range": {"start": None, "end": None},
            "note": (
                "本回答由 LLM 基于只读查询结果生成；"
                "docs 为命中的口径/元数据来源（含文件与行号），"
                "tables 与 executed_sql 为可核对的实际执行痕迹。"
                "指标口径的唯一权威是 sql/metadata/metrics.md。"
            ),
        },
        "generated_at": now_iso(),
    }
