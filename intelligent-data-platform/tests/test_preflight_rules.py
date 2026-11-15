# -*- coding: utf-8 -*-
"""预检规则的单元测试：把"哪些东西必须在写库前就被挡住"钉成可执行断言。

只测纯规则（金额精度、表头一致、主键/外键判定入口），不碰数据库、不跑装载。
真实数据上的整链预检在 D1 证据文档里用一条命令记录。
"""

from __future__ import annotations

import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data-generator"))

from src import preflight  # noqa: E402


@pytest.mark.parametrize("value", ["0", "12", "12.3", "12.34", "-5.00", "-0.01"])
def test_money_pattern_accepts_two_decimals(value):
    assert preflight._MONEY_RE.match(value) is not None


@pytest.mark.parametrize("value", ["12.345", "1e5", "1,234.00", "abc", " 12.34"])
def test_money_pattern_rejects_more_than_two_decimals(value):
    assert preflight._MONEY_RE.match(value) is None


def _write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def test_check_file_reports_header_mismatch(tmp_path):
    path = tmp_path / "orders.csv"
    _write_csv(path, ["order_id", "user_id"], [["1", "1"]])
    problems: list[str] = []
    preflight.check_file("orders.csv", str(path), problems, {})
    assert any("表头不符" in p for p in problems)


def test_check_file_flags_excess_money_precision(tmp_path):
    path = tmp_path / "orders.csv"
    header = preflight.COLS["orders.csv"]
    row = ["1", "1", "1", "1", "12.345", "PAID", "2011-11-01 08:16:00", "", ""]
    _write_csv(path, header, [row])
    problems: list[str] = []
    preflight.check_file("orders.csv", str(path), problems, {})
    assert any("两位小数" in p for p in problems)


def test_check_file_flags_non_monotonic_event_time(tmp_path):
    path = tmp_path / "tianchi_behavior.csv"
    header = preflight.COLS["tianchi_behavior.csv"]
    rows = [
        ["4" + "0" * 10 + "1", "VIEW", "10000001", "10123456", "c", "UNKNOWN", "未知", "2017-12-01 00:00:05"],
        ["4" + "0" * 10 + "2", "VIEW", "10000001", "10123456", "c", "UNKNOWN", "未知", "2017-12-01 00:00:01"],
    ]
    _write_csv(path, header, rows)
    problems: list[str] = []
    preflight.check_file("tianchi_behavior.csv", str(path), problems, {})
    assert any("逆序" in p for p in problems)


def test_unknown_file_is_reported_missing(tmp_path):
    problems: list[str] = []
    preflight.check_file("user.csv", str(tmp_path / "nope.csv"), problems, {})
    assert any("缺文件" in p for p in problems)
