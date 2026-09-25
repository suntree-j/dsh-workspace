"""生成 Kafka 实时事件。

用法：
    python -m src.generate_events                    # 发送一轮全部事件
    python -m src.generate_events --behavior 5000    # 覆盖行为事件条数
    python -m src.generate_events --topics order_event,payment_event
    python -m src.generate_events --continuous       # 持续发送
    python -m src.generate_events --rate 500         # 限速 500 条/秒

前置条件：
    必须先执行 python -m src.generate_mysql_data，
    以生成 state/dataset_snapshot.json。
    事件内容取自该快照，从而保证 Kafka 事件与 MySQL 数据一致
    （order_id / payment_id / refund_id / 金额 / 时间均对应真实记录）。

事件格式依据 docs/sprint/SPRINT_0.md 第 9 节
与 sql/metadata/kafka_topics.md。
"""

from __future__ import annotations

import argparse
import random
import sys
import time

from .common import get_logger
from .config import load_config
from .dataset import load_snapshot
from .kafka_events import (
    ALL_TOPICS,
    build_events,
)
from .kafka_producer import KafkaPublisher, verify_topics, wait_for_broker

log = get_logger("generate_events")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.generate_events",
        description="向 Kafka 产生实时事件",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--topics",
        type=str,
        default=None,
        help=f"只发送指定 Topic，逗号分隔。可选：{', '.join(ALL_TOPICS)}",
    )
    parser.add_argument(
        "--behavior",
        type=int,
        default=None,
        help="覆盖行为事件条数（默认取 GEN_BEHAVIOR_EVENT_COUNT）",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=0.0,
        help="限速（条/秒）。0 表示不限速",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="最多发送多少条后退出。0 表示不限制",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="持续向 Kafka 发送事件（每轮重新生成，直至 Ctrl+C）",
    )
    parser.add_argument(
        "--round-behavior",
        type=int,
        default=2000,
        help="持续发送模式下每轮行为事件条数",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="随机种子（覆盖快照中的种子）"
    )
    parser.add_argument(
        "--snapshot", type=str, default=None, help="指定数据集快照路径"
    )
    return parser


def _parse_topics(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    topics = [t.strip() for t in raw.split(",") if t.strip()]
    invalid = [t for t in topics if t not in ALL_TOPICS]
    if invalid:
        raise SystemExit(
            f"未知的 Topic：{', '.join(invalid)}\n可选值：{', '.join(ALL_TOPICS)}"
        )
    return topics


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    topics = _parse_topics(args.topics)

    app_cfg = load_config()
    gen_cfg = app_cfg.generate

    log.info("=" * 60)
    log.info("生成 Kafka 实时事件")
    log.info("=" * 60)

    # 1. 载入快照（保证与 MySQL 一致）
    from pathlib import Path

    snapshot = Path(args.snapshot) if args.snapshot else None
    dataset = load_snapshot(snapshot)
    log.info("数据集规模：%s", dataset.summary())

    seed = args.seed if args.seed is not None else dataset.seed
    rng = random.Random(seed)

    # 2. 等待 broker 并确认 Topic 就绪
    wait_for_broker(app_cfg.kafka)
    selected = topics or ALL_TOPICS
    partitions = verify_topics(app_cfg.kafka, selected)
    for topic, parts in partitions.items():
        log.info("  Topic %-16s 分区数 %d", topic, parts)

    # 3. 发送
    publisher = KafkaPublisher(app_cfg.kafka)
    sent_total = 0
    started = time.monotonic()
    interval = (1.0 / args.rate) if args.rate and args.rate > 0 else 0.0

    def _send(events) -> int:
        """发送一个事件流，返回条数。支持限速与总量上限。"""
        nonlocal sent_total
        count = 0
        for event in events:
            publisher.publish(event.topic, event.key, event.value)
            count += 1
            sent_total += 1
            if interval:
                time.sleep(interval)
            if args.count and sent_total >= args.count:
                break
        return count

    try:
        if args.continuous:
            log.info(
                "持续发送模式（每轮行为事件 %d 条），按 Ctrl+C 停止", args.round_behavior
            )
            round_no = 0
            while True:
                round_no += 1
                # 每轮用新种子重新生成行为事件，使内容与时间戳持续变化
                round_rng = random.Random(seed + round_no)
                events = build_events(
                    gen_cfg,
                    dataset,
                    round_rng,
                    topics=topics,
                    behavior_count=args.round_behavior,
                )
                n = _send(events)
                publisher.flush()
                publisher.assert_no_failures()
                elapsed = time.monotonic() - started
                log.info(
                    "第 %d 轮完成：本批 %d 条，累计 %d 条，耗时 %.1fs",
                    round_no, n, sent_total, elapsed,
                )
                if args.count and sent_total >= args.count:
                    log.info("已达到 --count 上限 %d，停止", args.count)
                    break
        else:
            events = build_events(
                gen_cfg,
                dataset,
                rng,
                topics=topics,
                behavior_count=args.behavior if args.behavior is not None else None,
            )
            _send(events)
            publisher.flush()

        publisher.assert_no_failures()
    except KeyboardInterrupt:
        log.warning("收到中断信号，正在刷新缓冲区 ...")
        publisher.flush(timeout=30)

    elapsed = time.monotonic() - started
    log.info("-" * 60)
    log.info("发送结果：")
    for topic, count in sorted(publisher.per_topic.items()):
        log.info("  %-16s %d 条", topic, count)
    log.info("  %-16s %d 条", "合计", publisher.delivered)
    if publisher.failed:
        log.error("  %-16s %d 条", "失败", publisher.failed)
    log.info("耗时 %.2f 秒（%.0f 条/秒）", elapsed, publisher.delivered / max(elapsed, 1e-6))
    log.info("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
