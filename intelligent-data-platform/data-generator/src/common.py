"""公共工具：日志、ID 生成、时间处理。

ID 与时间规则必须与 docs/metadata/kafka_topics.md 保持一致。
"""

from __future__ import annotations

import logging
import random
import sys
from datetime import datetime, timedelta, timezone

# 项目统一时区：Asia/Shanghai (UTC+8)
# 使用固定偏移而非 zoneinfo，避免容器内缺少 tzdata 导致失败。
CN_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

_LOGGER_NAME = "data-generator"


def get_logger(name: str | None = None) -> logging.Logger:
    """返回统一的 logger。"""
    logger = logging.getLogger(_LOGGER_NAME if name is None else f"{_LOGGER_NAME}.{name}")
    if not logging.getLogger(_LOGGER_NAME).handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root = logging.getLogger(_LOGGER_NAME)
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
    return logger


def now_cn() -> datetime:
    """当前时间（东八区，无微秒）。"""
    return datetime.now(CN_TZ).replace(microsecond=0)


def random_past_time(rng: random.Random, max_days_ago: int = 365) -> datetime:
    """随机过去时间（东八区）。"""
    seconds = rng.randint(0, max_days_ago * 24 * 3600)
    return (now_cn() - timedelta(seconds=seconds)).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    """转 ISO-8601 带时区偏移字符串，例如 2026-09-26T10:00:00+08:00。

    !! 重要：Kafka 事件请勿使用本函数 !!
    实测（Sprint 1）：Flink 的 JSON format **无法解析带时区偏移的
    ISO-8601 字符串**（它期望 'yyyy-MM-dd HH:mm:ss'）。当 format 配置为
    `json.ignore-parse-errors = 'true'` 时不会报错，而是把该字段
    **静默置为 NULL**，最终导致 Kafka sink 的
    StreamRecordTimestampInserter 抛 NullPointerException：
        Could not forward element to next operator
        Caused by: java.lang.NullPointerException
          at StreamRecordTimestampInserter.processElement

    因此事件时间统一使用 to_sql_datetime()（'yyyy-MM-dd HH:mm:ss'）：
    Flink 与 Doris 均可直接解析，且与项目统一时区 Asia/Shanghai 一致。
    本函数保留用于文档示例与人工排查
    （SPRINT_0.md 第 9 节的示例即为此格式）。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(CN_TZ).replace(microsecond=0).isoformat()


def to_sql_datetime(dt: datetime) -> str:
    """转 'yyyy-MM-dd HH:mm:ss.SSS'（Kafka 事件时间字段的标准格式）。

    !! 为什么必须带毫秒（.000）!!
    这是本项目最容易踩的坑，实测 + 官方文档确认：

    Flink 的 JSON format 选项 `json.timestamp-format.standard` 默认值为
    'SQL'，它按
        TIMESTAMP     -> "yyyy-MM-dd HH:mm:ss.s{precision}"（如 2020-12-30 12:13:14.123）
        TIMESTAMP_LTZ -> "yyyy-MM-dd HH:mm:ss.s{precision}'Z'"
    解析时间字段（见 Flink 1.20 JSON format 文档）。

    也就是说**必须带小数秒**。若写成 "2025-07-16 13:42:44"（无 .000），
    解析会失败；又因 source 配置了 json.ignore-parse-errors='true'，
    该字段被**静默置为 NULL**，最终触发 Kafka sink 的
        java.lang.NullPointerException
          at StreamRecordTimestampInserter.processElement
    而 SQL Gateway 只回报 "Failed to fetchResults"，极难定位。

    同时该格式也被 Doris DATETIME 直接接受，因此 MySQL 写入与
    Kafka 事件可以使用同一格式，避免两套时间表示。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S.000")


class EventIdGenerator:
    """事件 ID 生成器。

    格式：evt_<prefix><序号>
      订单事件   evt_1xxxx
      支付事件   evt_2xxxx
      退款事件   evt_3xxxx
      行为事件   evt_4xxxx
    与 docs/sprint/SPRINT_0.md 第 9 节示例保持一致。
    """

    def __init__(self, prefix: int, start: int = 1) -> None:
        if not 1 <= prefix <= 9:
            raise ValueError("prefix 必须是 1-9 的单个数字")
        self._prefix = prefix
        self._next = start

    def next(self) -> str:
        value = f"evt_{self._prefix}{self._next:04d}"
        self._next += 1
        return value
