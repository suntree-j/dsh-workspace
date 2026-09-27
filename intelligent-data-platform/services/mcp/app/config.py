"""MCP 服务配置（Sprint 10）。

## 这个服务**故意**没有的东西

    没有数据库主机 / 端口 / 账号 / 口令，
    没有 Doris / MySQL 驱动依赖，没有任何 .env 里的数据层键。

MCP 服务的能力上限 = 只读数据服务 `POST /data/api/query` 的能力上限。
它拿不到数据库凭据，因此即使 MCP 服务被完全控制，
攻击者也**没有**可用的数据库入口 —— 这是 `AGENTS.md` §10.3「权限最小化」
与 §10.1「Agent 不允许直接拥有数据库管理员权限」在 MCP 这一层的落地形态。

`MCP_DATA_API_BASE` 是**唯一**必需项，且它只能指向只读数据服务：
换成任何别的东西都不行（那不是一个能凭它写库的东西）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["Settings", "load_settings"]

DEFAULT_DATA_API_BASE = "http://127.0.0.1:8000"

# 单次数据服务调用的超时。取 30s：数据服务侧 Doris 查询超时是 15s，
# 这里必须比它大，否则会出现"数据服务还在正常查、MCP 已经超时报错"的假失败。
# 与 `services/agent/app/tools.py` 的 API_TIMEOUT 保持同一个值 ——
# 两条取数路径的超时语义必须一致，否则"MCP 路径更慢"会变成一个假结论。
API_TIMEOUT = 30


@dataclass(frozen=True)
class Settings:
    """MCP 服务运行配置（全部来自环境变量，无凭据）。"""

    # 名称与版本：会出现在 MCP 的 serverInfo 里，便于客户端核对连的是谁
    name: str
    version: str

    # 只读数据服务地址（MCP 的唯一数据出口）
    data_api_base: str

    # 监听地址。默认只监听回环：对外必须经 Nginx，不能直接暴露 MCP 端口
    host: str
    port: int
    # streamable-http 的 MCP 端点路径
    http_path: str

    # 单次查询行数上限（与数据服务侧 MAX_LIMIT 同量级，再收敛一次）
    query_limit: int

    # 单次数据服务调用超时（秒）
    api_timeout: float


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    return float(raw)


def load_settings() -> Settings:
    """从环境变量构造配置。

    为什么不读 `.env`：MCP 服务以专用系统用户运行，而 `.env` 是
    `root:dpapi 0640`（含数据层口令，零容忍规范 `AGENTS.md` §7）。
    与其把它加进可读组，不如**根本不让这个进程碰到凭据文件** ——
    最小权限不是"少读几个键"，而是"没有那个文件"。
    systemd 单元只注入下面这几个非敏感变量（见 services/mcp/deploy/）。
    """
    return Settings(
        name=os.environ.get("MCP_SERVER_NAME", "data-platform-readonly-mcp"),
        version=os.environ.get("MCP_SERVER_VERSION", "1.0.0"),
        data_api_base=os.environ.get("MCP_DATA_API_BASE", DEFAULT_DATA_API_BASE).rstrip("/"),
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=_env_int("MCP_PORT", 8200),
        http_path=os.environ.get("MCP_HTTP_PATH", "/mcp"),
        query_limit=_env_int("MCP_QUERY_LIMIT", 200),
        api_timeout=_env_float("MCP_API_TIMEOUT", API_TIMEOUT),
    )
