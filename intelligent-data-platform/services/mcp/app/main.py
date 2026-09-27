"""MCP 服务入口（Sprint 10）。

    python -m app.main --transport streamable-http   # 部署形态（默认）
    python -m app.main --transport stdio             # 本地宿主形态

systemd 单元（`services/mcp/deploy/data-platform-mcp.service`）用的就是第一种，
工作目录 `services/mcp`，因此 `app` 这个包能被直接 import。

## 为什么入口单独一个文件，而不是把 `main()` 写在 server.py 里

    `server.py` 是"服务是什么"（工具实现），`main.py` 是"怎么跑起来"
    （传输、监听地址、日志）。分开之后：
      - 测试可以直接 `from app.server import build_server` 装配服务，
        不会因为"import 了入口"就把 uvicorn 也拖进来；
      - stdio 形态由桌面宿主按约定启动，它要的也是 `python -m app.main`，
        与部署形态共用同一个入口，**能力面不会因为入口不同而漂移**。
"""

from __future__ import annotations

import logging

from .server import main

__all__ = ["main", "run"]

log = logging.getLogger("mcp.main")


def run() -> int:
    """供打包入口（console_scripts / 测试）调用的零参形式。"""
    return main([])


if __name__ == "__main__":
    raise SystemExit(main())
