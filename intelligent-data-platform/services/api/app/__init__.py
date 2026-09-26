"""数据服务应用包。

模块划分：
    config.py        配置（口令一律来自 .env）
    sqlguard.py      SQL 安全守卫（纯函数，可单测）
    doris.py         只读 Doris 访问
    repository.py    所有 SQL 常量与结果整形
    metrics_doc.py   指标口径字典（解析 sql/metadata/metrics.md）
    envelope.py      统一响应信封与错误模型
    main.py          FastAPI 路由
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
