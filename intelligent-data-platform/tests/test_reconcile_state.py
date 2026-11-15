"""对账三态判定的单元测试（D2/D4）。

只测纯函数 `classify_reconcile_state`：它把"同一区间内两侧是否一致"拆成
consistent / realtime_behind / diverged / unknown 四态。这个函数存在的意义是
**不把恢复期误报成数据错误，也不把差异当成"正在恢复"糊过去**，所以四态
都必须有用例钉住，尤其是"实时 < 离线 但只是落后"与"两侧都有值却不相等"的界线。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from app.repository import classify_reconcile_state  # noqa: E402


def _side(windows: int, gmv: str, paid: str) -> dict:
    return {"windows": windows, "gmv": gmv, "paid": paid}


def test_consistent_when_windows_and_amounts_match() -> None:
    state, detail = classify_reconcile_state(
        _side(40358, "17067427.47", "17067427.47"),
        _side(40358, "17067427.47", "17067427.47"),
    )
    assert state == "consistent"
    assert "完全相等" in detail


def test_realtime_behind_is_not_a_pass() -> None:
    """恢复期：实时侧窗口更少、金额更小 —— 判为落后，需二次采样，不是通过。"""
    state, detail = classify_reconcile_state(
        _side(39929, "16746946.14", "16746946.14"),
        _side(40358, "17067427.47", "17067427.47"),
    )
    assert state == "realtime_behind"
    assert "重放" in detail or "恢复" in detail
    assert "需相隔一段时间再采一次确认" in detail


def test_realtime_behind_also_covers_fewer_windows_same_amount() -> None:
    state, _ = classify_reconcile_state(
        _side(40357, "17067427.47", "17067427.47"),
        _side(40358, "17067427.47", "17067427.47"),
    )
    assert state == "realtime_behind"


def test_same_windows_but_lower_gmv_is_still_behind() -> None:
    """窗口数相同但金额偏小：可能是窗口未收满，单次采样无法与"漏数"区分。

    所以判为 `realtime_behind`（待再采样确认），而不是直接判失败——
    这也是本函数存在的理由：不把恢复期误报成数据错误。
    """
    state, detail = classify_reconcile_state(
        _side(40358, "17060000.00", "17067427.47"),
        _side(40358, "17067427.47", "17067427.47"),
    )
    assert state == "realtime_behind"
    assert "确认是否收敛" in detail


def test_diverged_when_realtime_larger() -> None:
    state, _ = classify_reconcile_state(
        _side(40359, "17068000.00", "17068000.00"),
        _side(40358, "17067427.47", "17067427.47"),
    )
    assert state == "diverged"


def test_unknown_when_side_missing() -> None:
    assert classify_reconcile_state({}, _side(1, "1.00", "1.00"))[0] == "unknown"
    assert classify_reconcile_state(_side(1, "1.00", "1.00"), {})[0] == "unknown"


def test_unknown_when_windows_missing() -> None:
    state, _ = classify_reconcile_state({"gmv": "1.00"}, {"gmv": "1.00"})
    assert state == "unknown"


def test_paid_mismatch_blocks_consistent() -> None:
    """GMV 相等但支付金额不等，不能判通过。"""
    state, _ = classify_reconcile_state(
        _side(40358, "17067427.47", "17060000.00"),
        _side(40358, "17067427.47", "17067427.47"),
    )
    assert state == "diverged"


def test_decimal_comparison_ignores_string_formatting() -> None:
    """`17067427.470` 与 `17067427.47` 是同一个数，不该判为不一致。"""
    state, _ = classify_reconcile_state(
        _side(10, "17067427.470", "17067427.470"),
        _side(10, "17067427.47", "17067427.47"),
    )
    assert state == "consistent"
