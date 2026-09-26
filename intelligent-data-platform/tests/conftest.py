"""pytest 全局引导。

作用：把仓库根目录与两个服务目录放进 sys.path，使测试可以按包路径导入服务层代码，
无论用 `pytest` 还是 `python -m pytest` 启动。

为什么服务目录也要加：
    `services/api/app/` 与 `services/agent/app/` **都叫 `app`**，
    是两个独立部署单元（各自的 venv 与 systemd 单元）。
    它们无法同时按 `app.*` 导入 —— 谁先进 sys.path 谁生效。
    由于 `tests/test_agent.py` 依赖 `app.agent`（Agent 包），
    这里统一把 **agent 目录放在最前**，并在测试里避免同时导入两边的 `app.*`。
    跨服务的行为由 HTTP 冒烟测试（tests/smoke/test_agent_api.py）覆盖，
    那才是它们真实的交互方式。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = REPO_ROOT / "services" / "agent"

for path in (str(AGENT_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        # agent 目录插到最前，保证 `import app.agent` 解析到 Agent 服务
        sys.path.insert(0, path)
