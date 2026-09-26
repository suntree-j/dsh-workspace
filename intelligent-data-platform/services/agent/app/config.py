"""Agent 服务配置。

设计原则（与 `services/api/app/config.py` 保持一致）：
    口令 / API Key **只从 .env 注入**，代码里不允许出现任何默认凭据；
    缺少必需项时**启动即失败**，而不是带着空 Key 跑起来
    （那样会在用户第一次提问时才炸，且错误信息离根因很远）。

为什么 Agent 单独一个配置类：
    Agent 是独立进程（systemd 单元 `data-platform-agent`），
    与只读数据服务 `data-platform-api` 互不影响：
      - Agent 挂掉不影响看板；
      - Agent 被限流/超时也不会拖慢数据服务。
    两者唯一的耦合是 HTTP —— Agent 通过 `DATA_API_BASE` 调用数据服务，
    **不直连数据库**。这样"Agent 只能用只读能力"这件事由进程边界保证，
    而不是靠代码自觉。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 仓库根目录：<repo>/services/agent/app/config.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]


class ConfigError(RuntimeError):
    """配置缺失或非法。启动阶段抛出，直接阻止服务启动。"""


def _read_env_file(path: Path) -> dict[str, str]:
    """解析 KEY=VALUE 形式的 .env（不做 shell 求值）。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# 从 .env 读取的键（白名单，避免把整个进程环境混进来）
_AGENT_KEYS = {
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_TIMEOUT",
    "LLM_MAX_TOKENS",
    "LLM_THINKING",
    "AGENT_MAX_TOOL_ROUNDS",
    "AGENT_QUERY_LIMIT",
    "AGENT_TOTAL_TIMEOUT",
    "AGENT_TEMPERATURE",
    "DATA_API_BASE",
    "AGENT_HOST",
    "AGENT_PORT",
    "AGENT_TITLE",
    "AGENT_VERSION",
}

# 允许通过环境变量覆盖的已知键（含 API_ 前缀，便于与数据服务共用 .env）
_ENV_PREFIXES = ("LLM_", "AGENT_")


def load_env() -> dict[str, str]:
    """读取 .env：显式路径 > 仓库根 .env > 服务器部署路径。

    环境变量优先级最高（便于容器 / CI 覆盖）。
    """
    candidates: list[Path] = []
    explicit = os.environ.get("DATA_PLATFORM_ENV")
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(REPO_ROOT / ".env")
    candidates.append(Path("/opt/data-platform/.env"))

    file_values: dict[str, str] = {}
    for candidate in candidates:
        values = _read_env_file(candidate)
        if values:
            file_values = values
            break

    merged = dict(file_values)
    merged.update(
        {
            key: value
            for key, value in os.environ.items()
            if key in _AGENT_KEYS or key.startswith(_ENV_PREFIXES)
        }
    )
    return merged


@dataclass(frozen=True)
class Settings:
    """Agent 运行配置。"""

    title: str
    version: str
    host: str
    port: int

    # 数据服务地址（Agent 的唯一数据出口）
    data_api_base: str

    # LLM
    llm_provider: str
    llm_model: str
    llm_api_key: str
    llm_base_url: str
    llm_timeout: float
    llm_max_tokens: int
    llm_thinking: bool
    temperature: float

    # Agent 行为约束
    max_tool_rounds: int
    query_limit: int
    total_timeout: float

    @property
    def llm_configured(self) -> bool:
        """是否配置了可用的 API Key。

        未配置时服务仍可启动，`/ask` 会明确返回"未配置"并给出配置步骤 ——
        比启动直接失败更友好：部署后可以先做健康检查与自检，
        再去申请 Key。但**绝不会**在缺少 Key 时假装回答。
        """
        return bool(self.llm_api_key)


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是整数，实际为 {raw!r}") from exc


def _env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 必须是数字，实际为 {raw!r}") from exc


def _env_bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def load_settings(env_file: Path | None = None) -> Settings:
    """构造配置对象。

    只有 `data_api_base` 是硬性要求（没有它 Agent 查不到任何数据）；
    LLM Key 允许缺失，见 `Settings.llm_configured` 的说明。
    """
    env = load_env()

    return Settings(
        title=env.get("AGENT_TITLE", "批流一体智能数据分析平台 · 数据问答 Agent"),
        version=env.get("AGENT_VERSION", "1.0.0"),
        host=env.get("AGENT_HOST", "127.0.0.1"),
        port=_env_int(env, "AGENT_PORT", 8100),
        # 容器外部署时数据服务就在本机 8000 端口（systemd 托管，见 install-web.sh）
        data_api_base=env.get("DATA_API_BASE", "http://127.0.0.1:8000").rstrip("/"),
        llm_provider=env.get("LLM_PROVIDER", "deepseek"),
        # 默认 deepseek-flash：本项目的上下文很短（口径 + 表结构 + 一条 SQL），
        # 不需要 pro；flash 支持 Tool Calls 且价格约为 pro 的 1/4.5。
        llm_model=env.get("LLM_MODEL", "deepseek-flash"),
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_base_url=env.get("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_timeout=_env_float(env, "LLM_TIMEOUT", 60.0),
        llm_max_tokens=_env_int(env, "LLM_MAX_TOKENS", 2048),
        # 默认关闭思考模式，理由见 app/llm.py 的模块说明（不是省事，是确定性）
        llm_thinking=_env_bool(env, "LLM_THINKING", False),
        # 思考模式不支持 temperature；非思考模式下取 0 ——
        # 同一句提问应当尽量生成同一条 SQL，便于复现与排错。
        temperature=_env_float(env, "AGENT_TEMPERATURE", 0.0),
        max_tool_rounds=_env_int(env, "AGENT_MAX_TOOL_ROUNDS", 6),
        query_limit=_env_int(env, "AGENT_QUERY_LIMIT", 200),
        total_timeout=_env_float(env, "AGENT_TOTAL_TIMEOUT", 120.0),
    )
