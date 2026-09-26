"""数据问答 Agent（Sprint 7）：LLM + Tool Calling。

定位：
    这是「智能层」的第一个可运行形态 —— 自然语言问题进，带来源说明的回答出。
    它**不直连数据库**：所有取数都经 `services/api` 的只读接口，
    因此 Agent 的能力边界与一个只读用户完全一致（进程边界即权限边界）。

启动：
    uvicorn app.main:app --host 127.0.0.1 --port 8100
    （生产由 systemd 托管，见 deploy/systemd/data-platform-agent.service）

与数据服务的关系：
    Nginx 把 /data/agent/ 反代到本服务，/data/api/ 反代到数据服务。
    对外只有一个入口，看板通过同源路径调用，不需要额外开端口。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .agent import SYSTEM_PROMPT, Agent, describe_tools
from .config import ConfigError, Settings, load_settings
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
        "自然语言 → 指标口径检索 → SQL 生成 → 安全检查 → 只读执行 → 带来源的回答。\n"
        "所有取数都经只读数据服务，Agent 自身没有数据库凭据。"
    ),
    root_path="/data/agent",  # 经 Nginx 反代后的对外路径（保证 /docs 链接正确）
)


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
            "endpoints": ["/health", "/tools", "POST /ask", "/prompt"],
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


@app.get("/health", summary="健康检查")
def health() -> dict[str, Any]:
    """报告 Agent 与数据服务、LLM 的可用性。

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

    status = "ok" if (api_ok and SETTINGS.llm_configured) else "degraded"
    return {
        "data": {
            "status": status,
            "data_api": {"ok": api_ok, "base": SETTINGS.data_api_base, "detail": api_detail},
            "llm": {
                "configured": SETTINGS.llm_configured,
                "provider": SETTINGS.llm_provider,
                "model": SETTINGS.llm_model,
                "base_url": SETTINGS.llm_base_url,
                "thinking_mode": SETTINGS.llm_thinking,
            },
            "limits": {
                "max_tool_rounds": SETTINGS.max_tool_rounds,
                "query_limit": SETTINGS.query_limit,
                "total_timeout": SETTINGS.total_timeout,
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
    安全审查只需要看这四个工具的声明与实现，不需要读提示词去猜。
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


@app.post("/ask", summary="提问（自然语言 → 带来源的回答）")
def ask(payload: AskRequest) -> dict[str, Any]:
    """回答一个自然语言问题。

    响应里除了 `answer`，还带上：
      - `tables`       用了哪些表（来源可追溯）
      - `executed_sql` 实际执行的 SQL（服务端返回的改写后语句，可核对）
      - `steps`        每一步调了什么工具、成功与否（过程可审计）

    这三项就是 `AGENTS.md` 第 10.1 节「最终回答必须能够说明数据来源」
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

    result = Agent(SETTINGS).ask(payload.question)
    return {
        "data": result.as_dict(),
        "source": {
            "tables": result.tables,
            "metric_definitions": [],
            "time_range": {"start": None, "end": None},
            "note": (
                "本回答由 LLM 基于只读查询结果生成；"
                "tables 与 executed_sql 为可核对的实际执行痕迹。"
                "指标口径的唯一权威是 sql/metadata/metrics.md。"
            ),
        },
        "generated_at": now_iso(),
    }
