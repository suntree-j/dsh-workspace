"""tests/smoke 共用前置条件与容器执行工具。

为什么放在 conftest.py：
    Sprint 0（test_infrastructure.py）与 Sprint 1（test_realtime.py）
    都需要「定位 docker、读取 .env、在容器内执行命令、判断容器是否在运行」。
    这些属于测试基础设施，由 pytest 通过 conftest.py 自动注入，
    各测试文件只关心断言，既不重复实现，也不互相 import。

约定：
    - 所有容器操作都通过 `docker exec` 在**容器内部**执行，
      因此不要求宿主机安装 mysql / kafka 客户端，也不依赖宿主端口映射；
    - 服务未启动时一律 `pytest.skip`（优雅跳过），而不是误报失败。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Sprint 0 核心服务
CORE_CONTAINERS = ("mysql", "kafka", "minio")
# Doris
DORIS_CONTAINERS = ("doris-fe", "doris-be")
# Sprint 1 实时链路
REALTIME_CONTAINERS = (
    "flink-jobmanager",
    "flink-taskmanager",
    "flink-sql-gateway",
    "flink-jobs",
)


# ------------------------------------------------------------
# .env 读取
# ------------------------------------------------------------
def read_env() -> dict[str, str]:
    """读取仓库根目录 .env（键值对解析，不做 shell 求值）。"""
    env: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return env
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def cfg(name: str, default: str = "") -> str:
    """优先取环境变量（支持容器内运行），其次 .env，最后默认值。"""
    return os.environ.get(name) or read_env().get(name) or default


# ------------------------------------------------------------
# docker 可执行文件定位
# ------------------------------------------------------------
def resolve_docker() -> str | None:
    """定位 docker 可执行文件。

    Windows 上 Docker Desktop 安装后可能尚未加入当前 shell 的 PATH，
    因此额外探测默认安装位置，避免 pytest 因找不到命令而误报跳过。
    """
    found = shutil.which("docker")
    if found:
        return found

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        Path("/usr/bin/docker"),
        Path("/usr/local/bin/docker"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


# ------------------------------------------------------------
# 容器执行封装
# ------------------------------------------------------------
class DockerRunner:
    """`docker exec` 的薄封装，供各冒烟测试复用。"""

    def __init__(self, cli: str) -> None:
        self.cli = cli

    def run(
        self, container: str, *args: str, timeout: int = 60
    ) -> subprocess.CompletedProcess:
        """在容器内执行命令，返回完整结果（不抛异常）。"""
        return subprocess.run(
            [self.cli, "exec", container, *args],
            capture_output=True, text=True, timeout=timeout, check=False, errors="replace",
        )

    def out(self, container: str, *args: str, timeout: int = 60) -> str:
        """在容器内执行命令并返回 stdout；非 0 退出即断言失败。"""
        result = self.run(container, *args, timeout=timeout)
        assert result.returncode == 0, (
            f"docker exec {container} {' '.join(args)} 失败（exit {result.returncode}）\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        return result.stdout

    def lines(self, container: str, *args: str, timeout: int = 60) -> list[str]:
        """在容器内执行命令并返回非空行列表。"""
        return [ln.strip() for ln in self.out(container, *args, timeout=timeout).splitlines()
                if ln.strip()]

    def running(self, container: str) -> bool:
        """容器是否处于运行状态。"""
        result = subprocess.run(
            [self.cli, "inspect", "-f", "{{.State.Running}}", container],
            capture_output=True, text=True, check=False,
        )
        return result.stdout.strip() == "true"

    def inspect(self, container: str, template: str) -> str:
        """按模板读取容器信息（注意：这是 docker inspect，不是 docker exec）。"""
        result = subprocess.run(
            [self.cli, "inspect", "-f", template, container],
            capture_output=True, text=True, check=False,
        )
        return result.stdout.strip()

    def health(self, container: str) -> str:
        """容器 healthcheck 状态：healthy / starting / unhealthy / missing。"""
        return self.inspect(container, "{{.State.Health.Status}}") or "missing"


# ------------------------------------------------------------
# fixtures
# ------------------------------------------------------------
@pytest.fixture(scope="session")
def docker() -> DockerRunner:
    """可用的 docker CLI；不可用时优雅跳过。"""
    cli = resolve_docker()
    if not cli:
        pytest.skip(
            "未找到 docker 可执行文件。\n"
            "  请安装并启动 Docker Desktop，或将其加入 PATH：\n"
            r"  C:\Program Files\Docker\Docker\resources\bin"
        )

    try:
        probe = subprocess.run(
            [cli, "info"], capture_output=True, text=True, timeout=30, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"docker 不可用：{exc}")

    if probe.returncode != 0:
        pytest.skip(
            "Docker daemon 未运行。\n"
            "  请启动 Docker Desktop 并等待其显示 Engine running，"
            "然后执行 docker compose up -d"
        )

    return DockerRunner(cli)


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    """仓库 .env 内容。"""
    values = read_env()
    if not values:
        pytest.skip("未找到 .env，请先执行： cp .env.example .env")
    return values


@pytest.fixture(scope="session")
def mysql_creds(env: dict[str, str]) -> tuple[str, str]:
    password = env.get("MYSQL_ROOT_PASSWORD", "")
    assert password, "MYSQL_ROOT_PASSWORD 未设置，请检查 .env"
    return "root", password


@pytest.fixture(scope="session")
def require_core(docker: DockerRunner) -> None:
    """Sprint 0 核心服务前置条件。"""
    missing = [name for name in CORE_CONTAINERS if not docker.running(name)]
    if missing:
        pytest.skip(
            f"以下核心容器未运行：{', '.join(missing)}。\n"
            "  请先执行： bash scripts/start.sh  （或 docker compose up -d）"
        )


@pytest.fixture(scope="session")
def require_doris(docker: DockerRunner) -> None:
    """Doris 相关用例的前置条件。"""
    missing = [name for name in DORIS_CONTAINERS if not docker.running(name)]
    if missing:
        pytest.skip(
            f"以下 Doris 容器未运行：{', '.join(missing)}。\n"
            "  Doris 首次启动需要 1~3 分钟，请稍后重试：\n"
            "  docker compose logs --tail=50 doris-fe doris-be"
        )


@pytest.fixture(scope="session")
def require_realtime(docker: DockerRunner, require_doris: None) -> None:
    """Sprint 1 实时链路前置条件（Flink 4 个容器 + Doris）。"""
    missing = [name for name in REALTIME_CONTAINERS if not docker.running(name)]
    if missing:
        pytest.skip(
            f"以下实时链路容器未运行：{', '.join(missing)}。\n"
            "  请先执行： docker compose up -d\n"
            "  首次启动需等 Flink 集群就绪、Routine Load 建立（约 3~5 分钟），"
            "可用 bash scripts/health-check.sh 确认。"
        )
