"""pytest 全局引导。

作用：把仓库根目录放进 sys.path，使测试可以按包路径导入服务层代码
（`services.api.app.*`），无论用 `pytest` 还是 `python -m pytest` 启动。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
