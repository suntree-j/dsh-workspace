"""D3：归档自检判据的纯函数单测。

背景（真实故障）：
    `behavior_event` 没有显式 retention，继承 broker 默认 168 小时。消息过期被删后，
    下一次归档读到 0 条，而湖仓 ODS 仍保留 10,098,974 行历史归档。
    旧判据写的是「整表行数 == 本次 Kafka 消息数」，于是
    「10,098,974 != 0」永远成立为**失败**，归档任务每晚失败，
    其后 11 个阶段全部 upstream_failed（Airflow 元数据里 archive_behavior 失败 24 次即此形态）。

    新判据按「本次覆盖的 dt 分区」比较，并把「本次 0 条」当作正常的信息态。

这组用例锁住三个必须区分的语义：
    1. 本次 0 条 → 通过（信息态），不能判失败；
    2. 本次 N 条、分区行数 == N → 通过；
    3. 本次 N 条、分区行数 < N → 失败（真丢数据），且不能因为整表大就放过。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_JOB = Path(__file__).resolve().parents[1] / "infrastructure" / "spark" / "jobs" / "archive_criteria.py"


def _load_criteria():
    """判据单独成文件，因此无需初始化 SparkSession。"""
    spec = importlib.util.spec_from_file_location("archive_job", _JOB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.archive_criteria


@pytest.fixture(scope="module")
def criteria():
    return _load_criteria()


def _by_name(rows):
    return {name: ok for name, ok, _detail in rows}


def test_empty_topic_is_informational_not_failure(criteria):
    """retention 清空后本次读到 0 条：必须通过（历史归档保留不动）。"""
    rows = criteria(kafka_count=0, archived_total=10_098_974, archived_touched=0, parsed_count=0)
    assert all(ok for _n, ok, _d in rows), rows
    assert any("无新消息" in name for name, _ok, _d in rows)


def test_full_replay_matches_touched_partitions(criteria):
    """本次全量重放 10,098,974 条，覆盖分区行数相同 → 通过。"""
    rows = criteria(
        kafka_count=10_098_974,
        archived_total=10_098_974,
        archived_touched=10_098_974,
        parsed_count=10_098_974,
    )
    assert all(ok for _n, ok, _d in rows), rows


def test_partial_write_fails_even_when_total_is_large(criteria):
    """本次写入少于消息数：即便整表很大也必须失败（防「整表大就放过」）。"""
    rows = criteria(
        kafka_count=1000,
        archived_total=10_098_974,
        archived_touched=997,
        parsed_count=1000,
    )
    status = _by_name(rows)
    assert status["本次覆盖分区行数 == Kafka 消息数"] is False
    assert status["整表行数不少于本次消息数（归档是 topic 的超集）"] is True
    assert not all(status.values())


def test_unparsable_messages_fail(criteria):
    """读到的消息里有解析失败的行：可解析数 != 消息数 → 失败。"""
    rows = criteria(
        kafka_count=1000,
        archived_total=10_098_974,
        archived_touched=1000,
        parsed_count=998,
    )
    status = _by_name(rows)
    assert status["可解析行数 == Kafka 消息数"] is False


def test_archive_smaller_than_topic_fails(criteria):
    """归档表行数少于本次消息数（例如被误清空）→ 失败。"""
    rows = criteria(
        kafka_count=2000,
        archived_total=1500,
        archived_touched=2000,
        parsed_count=2000,
    )
    status = _by_name(rows)
    assert status["整表行数不少于本次消息数（归档是 topic 的超集）"] is False


def test_criteria_are_pure(criteria):
    """同一输入多次调用结果一致，且不泄漏可变状态。"""
    a = criteria(0, 10, 0, 0)
    b = criteria(0, 10, 0, 0)
    assert a == b
