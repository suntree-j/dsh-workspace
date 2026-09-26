"""时间工具：项目统一时区 Asia/Shanghai（+08:00）。

为什么 Agent 服务自己带一份，而不是从数据服务 import：
    `services/agent` 与 `services/api` 是**两个独立部署单元**
    （两个 systemd 单元、两个 venv）。跨服务 import 会让它们无法独立打包与升级，
    也会把"Agent 不依赖数据服务内部实现"这条边界弄模糊。
    这几行代码的重复是刻意的，代价远小于耦合的代价。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:  # pragma: no cover - 取决于运行环境是否有 tzdata
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001 - 缺 tzdata 时退化为固定偏移
    # 本项目不跨时区，固定 +08:00 与 Asia/Shanghai 等价
    _TZ = timezone(timedelta(hours=8))


def now_local() -> datetime:
    """当前时间（Asia/Shanghai）。"""
    return datetime.now(_TZ)


def now_iso() -> str:
    """ISO-8601 字符串，带 +08:00 偏移。"""
    return now_local().isoformat(timespec="seconds")
