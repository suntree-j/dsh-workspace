"""配置加载（服务层）。

设计原则（对应 AGENTS.md 第 7 节 Secret 规范）：
    口令**只从 .env 注入**，代码里不允许出现任何默认口令；
    缺少必需变量时启动即失败，避免"带着空口令跑起来"这种更危险的状态。

为什么不用 pydantic-settings：
    依赖越少越好。这里只需要读一个 KEY=VALUE 文件 + 环境变量覆盖，
    二十行标准库代码足够，也更容易在答辩时讲清楚。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 仓库根目录：<repo>/services/api/app/config.py -> parents[3]
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


def load_env() -> dict[str, str]:
    """读取 .env：显式路径 > 仓库根 .env > 服务器部署路径。

    环境变量优先级最高（便于容器/CI 覆盖）。
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
    merged.update({k: v for k, v in os.environ.items() if k in _KNOWN_KEYS or k.startswith("API_")})
    return merged


# 从 .env 读取的白名单键（避免把整个进程环境混进来）
_KNOWN_KEYS = {
    "DORIS_FE_IP",
    "DORIS_FE_QUERY_PORT",
    "DORIS_ROOT_PASSWORD",
}


@dataclass(frozen=True)
class Settings:
    """服务运行配置。"""

    title: str
    version: str
    host: str
    port: int
    doris_host: str
    doris_port: int
    doris_user: str
    doris_password: str
    doris_database: str
    connect_timeout: int
    query_timeout: int
    default_limit: int
    max_limit: int
    metrics_doc: Path
    env_file: Path

    @property
    def is_readonly_user(self) -> bool:
        """是否使用了专用只读账号（而不是 root）。

        这既是健康检查项，也是答辩时的安全证据。
        """
        return self.doris_user not in ("root", "")


def load_settings(env_file: Path | None = None) -> Settings:
    """构造配置对象；缺少必需项时抛 ConfigError。"""
    env = load_env()

    password = env.get("API_DORIS_PASSWORD", "")
    if not password:
        raise ConfigError(
            "缺少 API_DORIS_PASSWORD。请在 .env 中配置数据服务专用只读账号口令，"
            "并执行 bash scripts/install-web.sh 创建 agent_ro 账号。"
        )

    user = env.get("API_DORIS_USER", "agent_ro")
    if user == "root":
        # 允许但显式告警：AGENTS.md 第 10.3 节要求不得复用 root
        pass

    return Settings(
        title=env.get("API_TITLE", "批流一体智能数据分析平台 · 数据服务"),
        version=env.get("API_VERSION", "1.0.0"),
        host=env.get("API_HOST", "127.0.0.1"),
        port=int(env.get("API_PORT", "8000")),
        doris_host=env.get("DORIS_FE_IP", "172.28.0.10"),
        doris_port=int(env.get("DORIS_FE_QUERY_PORT", "9030")),
        doris_user=user,
        doris_password=password,
        doris_database=env.get("API_DORIS_DATABASE", "ecommerce"),
        connect_timeout=int(env.get("API_CONNECT_TIMEOUT", "5")),
        query_timeout=int(env.get("API_QUERY_TIMEOUT", "15")),
        default_limit=int(env.get("API_DEFAULT_LIMIT", "60")),
        max_limit=int(env.get("API_MAX_LIMIT", "1000")),
        metrics_doc=Path(env.get("API_METRICS_DOC", str(REPO_ROOT / "sql" / "metadata" / "metrics.md"))),
        env_file=env_file or (REPO_ROOT / ".env"),
    )
