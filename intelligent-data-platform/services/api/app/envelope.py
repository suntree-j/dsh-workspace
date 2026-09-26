"""统一响应信封与错误模型。

为什么要有统一信封（对应 AGENTS.md 第 10.1 节）：
    「Agent 的最终回答必须能够说明数据来源」「不得伪造查询结果」。
    因此每个成功响应都必须带上 `source`：用了哪些表、引用了哪些指标口径、
    覆盖的时间范围。前端在每页底部把它显示出来，未来 Agent 也直接复用。

错误响应统一为 `{"error": {"code", "message", "detail"}}`，
message 一律中文，前端可以直接展示。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# 项目统一时区：Asia/Shanghai（+08:00）
#
# 说明：优先用 zoneinfo（依赖系统 tzdata）；若环境缺 tzdata（部分精简镜像），
# 退化为固定 +08:00 偏移 —— 本项目不跨时区，固定偏移是等价的。
try:  # pragma: no cover - 取决于运行环境
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001 - 缺失 tzdata 时退化
    _TZ = timezone(timedelta(hours=8))


def now_local() -> datetime:
    """当前时间（Asia/Shanghai）。"""
    return datetime.now(_TZ)


def now_iso() -> str:
    """ISO-8601 字符串，带 +08:00 偏移。"""
    return now_local().isoformat(timespec="seconds")


def envelope(
    data: Any,
    *,
    tables: list[str] | None = None,
    metric_definitions: list[dict[str, str]] | None = None,
    time_range: dict[str, str | None] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """构造统一响应体。

    tables 去重且保持顺序：一个接口往往由多条查询拼成，
    直接拼接会出现同一张表重复多次（前端"数据来源"一栏会很难看）。
    """
    unique_tables: list[str] = []
    for table in tables or []:
        if table and table not in unique_tables:
            unique_tables.append(table)

    unique_defs: list[dict[str, str]] = []
    seen_fields: set[str] = set()
    for definition in metric_definitions or []:
        field = definition.get("field", "")
        if field and field not in seen_fields:
            seen_fields.add(field)
            unique_defs.append(definition)

    return {
        "data": data,
        "source": {
            "tables": unique_tables,
            "metric_definitions": unique_defs,
            "time_range": time_range or {"start": None, "end": None},
            "note": note,
        },
        "generated_at": now_iso(),
    }


def error_body(code: str, message: str, detail: str = "") -> dict[str, Any]:
    """构造统一错误体。"""
    return {"error": {"code": code, "message": message, "detail": detail}}
