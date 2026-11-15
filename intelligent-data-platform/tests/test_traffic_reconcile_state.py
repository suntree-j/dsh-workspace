"""D2/D4 扩展：流量域的同区间判定（与交易域同一套四态语义）。

为什么单独测：
    交易域比 GMV/支付金额，流量域比 PV/窗口数——字段不同，但"一致 / 落后 / 偏高 / 未知"
    的判定必须共享同一套语义，否则两个域会在同一页面上给出互相矛盾的结论。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

try:
    from app.repository import classify_traffic_state  # noqa: E402
except ModuleNotFoundError as exc:  # 本地缺 mysql-connector 时跳过整组
    pytest.skip(f"依赖缺失，跳过：{exc}", allow_module_level=True)


def _side(windows: int, pv: str | None) -> dict:
    return {"windows": windows, "pv": pv, "uv": 1, "carts": 0, "buys": 0}


def test_consistent_when_windows_and_pv_equal() -> None:
    state, detail = classify_traffic_state(
        _side(12957, "10096157"), _side(12957, "10096157")
    )
    assert state == "consistent", detail


def test_behind_when_fewer_windows() -> None:
    state, detail = classify_traffic_state(
        _side(12000, "10096157"), _side(12957, "10096157")
    )
    assert state == "realtime_behind"
    assert "确认是否收敛" in detail


def test_behind_when_same_windows_but_lower_pv() -> None:
    """同窗口数但 PV 偏小：恢复期窗口可能只落一半，单次采样只能判"落后"。"""
    state, detail = classify_traffic_state(
        _side(12957, "9000000"), _side(12957, "10096157")
    )
    assert state == "realtime_behind"
    assert "确认是否收敛" in detail


def test_diverged_when_realtime_higher() -> None:
    state, detail = classify_traffic_state(
        _side(12957, "10096157"), _side(12957, "9000000")
    )
    assert state == "diverged"
    assert "偏高" in detail


def test_unknown_when_side_missing() -> None:
    state, _detail = classify_traffic_state({}, _side(1, "1"))
    assert state == "unknown"


def test_unknown_when_pv_missing() -> None:
    state, detail = classify_traffic_state(_side(10, None), _side(10, "100"))
    assert state == "unknown"
    assert "PV" in detail
