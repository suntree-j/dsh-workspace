"""Kafka 生产者封装（基于 confluent-kafka）。"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from collections.abc import Iterable

from confluent_kafka import KafkaException, Producer
from confluent_kafka.admin import AdminClient

from .common import get_logger
from .config import KafkaConfig

log = get_logger("kafka_producer")


class KafkaPublisher:
    """Kafka 生产者。

    使用 delivery 回调统计成功/失败，发送完成前必须 flush()，
    否则进程退出会丢失缓冲区中的消息。
    """

    def __init__(self, config: KafkaConfig) -> None:
        self._config = config
        self._producer = Producer(
            {
                "bootstrap.servers": config.bootstrap_servers,
                "client.id": config.client_id,
                "acks": config.acks,
                "linger.ms": config.linger_ms,
                "message.timeout.ms": config.message_timeout_ms,
            }
        )
        self._delivered = 0
        self._failed = 0
        self._errors: list[str] = []
        self._lock = threading.Lock()
        self._per_topic: Counter[str] = Counter()

    # --------------------------------------------------------
    # 回调
    # --------------------------------------------------------
    def _on_delivery(self, err, msg) -> None:  # noqa: ANN001
        with self._lock:
            if err is not None:
                self._failed += 1
                if len(self._errors) < 10:
                    self._errors.append(f"{msg.topic()}: {err}")
            else:
                self._delivered += 1
                self._per_topic[msg.topic()] += 1

    # --------------------------------------------------------
    # 发送
    # --------------------------------------------------------
    def publish(self, topic: str, key: str, value: dict) -> None:
        """发送单条 JSON 消息。"""
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        try:
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=payload,
                on_delivery=self._on_delivery,
            )
        except BufferError:
            # 本地队列满：先让回调有机会执行，再重试一次
            self._producer.poll(1.0)
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=payload,
                on_delivery=self._on_delivery,
            )
        # 触发回调，避免队列堆积
        self._producer.poll(0)

    def publish_all(self, events: Iterable) -> int:
        """批量发送，返回发送条数。"""
        count = 0
        for event in events:
            self.publish(event.topic, event.key, event.value)
            count += 1
        return count

    def flush(self, timeout: float = 60.0) -> None:
        """等待所有消息发送完成。"""
        remaining = self._producer.flush(timeout)
        if remaining > 0:
            raise RuntimeError(f"仍有 {remaining} 条消息未发送完成（超时 {timeout}s）")

    # --------------------------------------------------------
    # 统计
    # --------------------------------------------------------
    @property
    def delivered(self) -> int:
        with self._lock:
            return self._delivered

    @property
    def failed(self) -> int:
        with self._lock:
            return self._failed

    @property
    def per_topic(self) -> dict[str, int]:
        with self._lock:
            return dict(self._per_topic)

    @property
    def errors(self) -> list[str]:
        with self._lock:
            return list(self._errors)

    def assert_no_failures(self) -> None:
        if self.failed > 0:
            raise RuntimeError(
                f"有 {self.failed} 条消息发送失败。示例：{'; '.join(self.errors)}"
            )


def wait_for_broker(config: KafkaConfig, retries: int = 30, interval: float = 2.0) -> None:
    """等待 Kafka broker 就绪。"""
    admin = AdminClient({"bootstrap.servers": config.bootstrap_servers})
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            metadata = admin.list_topics(timeout=5)
            if metadata.brokers:
                log.info(
                    "Kafka 已就绪：%s（broker 数量 %d）",
                    config.bootstrap_servers,
                    len(metadata.brokers),
                )
                return
        except KafkaException as exc:
            last_error = exc
        if attempt % 5 == 1 or attempt == retries:
            log.warning("等待 Kafka 就绪（%d/%d）...", attempt, retries)
        time.sleep(interval)

    raise RuntimeError(f"Kafka {config.bootstrap_servers} 在超时前未就绪") from last_error


def verify_topics(config: KafkaConfig, required: Iterable[str]) -> dict[str, int]:
    """确认所需 Topic 存在，返回 {topic: partition_count}。"""
    admin = AdminClient({"bootstrap.servers": config.bootstrap_servers})
    metadata = admin.list_topics(timeout=10)

    result: dict[str, int] = {}
    missing: list[str] = []
    for topic in required:
        info = metadata.topics.get(topic)
        if info is None or info.error is not None:
            missing.append(topic)
        else:
            result[topic] = len(info.partitions)

    if missing:
        raise RuntimeError(
            f"缺少 Topic：{', '.join(missing)}。\n"
            "请确认 kafka-init 容器已成功执行：\n"
            "  docker compose logs kafka-init"
        )
    return result
