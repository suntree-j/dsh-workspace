"""Kafka 事件构造。

事件格式严格依据 docs/sprint/SPRINT_0.md 第 9 节
与 sql/metadata/kafka_topics.md。

关键约束：
    - order_event / payment_event / refund_event 的事件内容
      必须与 MySQL 中真实写入的订单、支付、退款一致；
    - behavior_event 的 user_id / product_id 必须真实存在；
    - 行为漏斗必须逐级收窄：
          count(VIEW) > count(CLICK) > count(CART) > count(BUY)

所有函数均为**生成器**，逐条产出事件，避免一次性占用大量内存
（event_count 可达数十万条）。
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import datetime, timedelta

from .common import EventIdGenerator, get_logger, now_cn, to_sql_datetime
from .config import GenerateConfig
from .dataset import Dataset, Order, Payment, Product, Refund, User

log = get_logger("events")

# ------------------------------------------------------------
# Topic 名称
# ------------------------------------------------------------
TOPIC_ORDER = "order_event"
TOPIC_PAYMENT = "payment_event"
TOPIC_REFUND = "refund_event"
TOPIC_BEHAVIOR = "behavior_event"

ALL_TOPICS = [TOPIC_ORDER, TOPIC_PAYMENT, TOPIC_REFUND, TOPIC_BEHAVIOR]

# ------------------------------------------------------------
# 事件类型
# ------------------------------------------------------------
EVENT_ORDER_CREATED = "ORDER_CREATED"
EVENT_PAYMENT_SUCCESS = "PAYMENT_SUCCESS"
EVENT_PAYMENT_FAILED = "PAYMENT_FAILED"
EVENT_REFUND_CREATED = "REFUND_CREATED"

EVENT_VIEW = "VIEW"
EVENT_CLICK = "CLICK"
EVENT_CART = "CART"
EVENT_FAVORITE = "FAVORITE"
EVENT_BUY = "BUY"

# 行为漏斗的固定顺序
BEHAVIOR_FUNNEL_ORDER = [EVENT_VIEW, EVENT_CLICK, EVENT_CART, EVENT_FAVORITE, EVENT_BUY]
# 主漏斗（FAVORITE 为旁支，不参与逐级收窄比较）
MAIN_FUNNEL = [EVENT_VIEW, EVENT_CLICK, EVENT_CART, EVENT_BUY]

BEHAVIOR_DEVICES = ["PC", "APP", "H5", "MINI_PROGRAM"]
BEHAVIOR_DEVICE_WEIGHTS = [25, 45, 15, 15]


class Event:
    """一条待发送的 Kafka 消息。"""

    __slots__ = ("topic", "key", "value")

    def __init__(self, topic: str, key: str, value: dict) -> None:
        self.topic = topic
        self.key = key
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover
        return f"Event(topic={self.topic!r}, key={self.key!r})"


# ============================================================
# 订单 / 支付 / 退款事件
# ============================================================
def order_events(
    orders: list[Order],
    products: list[Product] | None = None,
) -> Iterator[Event]:
    """订单事件：每个订单一条 ORDER_CREATED。分区键 = order_id。

    冗余维度字段（category_name / brand）：
        事件里带上商品类目与品牌，是数仓的常规做法 ——
        下游 Flink 无需再连维表即可出「类目维度」指标，
        避免为 join 引入额外的 connector 与维表状态。
        这不会破坏一致性：值直接取自同一批 product 主数据，
        并且也会写入 DWD 表，便于离线侧核对。
    """
    product_by_id = {p.product_id: p for p in (products or [])}
    id_gen = EventIdGenerator(prefix=1)
    for order in orders:
        product = product_by_id.get(order.product_id)
        value = {
            "event_id": id_gen.next(),
            "event_type": EVENT_ORDER_CREATED,
            "order_id": order.order_id,
            "user_id": order.user_id,
            "product_id": order.product_id,
            "quantity": order.quantity,
            "amount": float(order.amount),
            "event_time": to_sql_datetime(order.create_time),
        }
        # 仅在有维表时附加，保持向后兼容（缺省不产生这些字段）
        if product is not None:
            value["category_name"] = product.category_name
            value["brand"] = product.brand
        yield Event(topic=TOPIC_ORDER, key=str(order.order_id), value=value)


def payment_events(payments: list[Payment]) -> Iterator[Event]:
    """支付事件：支付成功 / 失败。分区键 = order_id。"""
    id_gen = EventIdGenerator(prefix=2)
    for payment in payments:
        event_type = (
            EVENT_PAYMENT_SUCCESS
            if payment.payment_status == "SUCCESS"
            else EVENT_PAYMENT_FAILED
        )
        yield Event(
            topic=TOPIC_PAYMENT,
            key=str(payment.order_id),
            value={
                "event_id": id_gen.next(),
                "event_type": event_type,
                "payment_id": payment.payment_id,
                "order_id": payment.order_id,
                "user_id": payment.user_id,
                "amount": float(payment.amount),
                "payment_method": payment.payment_method,
                "event_time": to_sql_datetime(payment.payment_time),
            },
        )


def refund_events(refunds: list[Refund]) -> Iterator[Event]:
    """退款事件。分区键 = order_id。"""
    id_gen = EventIdGenerator(prefix=3)
    for refund in refunds:
        yield Event(
            topic=TOPIC_REFUND,
            key=str(refund.order_id),
            value={
                "event_id": id_gen.next(),
                "event_type": EVENT_REFUND_CREATED,
                "refund_id": refund.refund_id,
                "order_id": refund.order_id,
                "user_id": refund.user_id,
                "refund_amount": float(refund.refund_amount),
                "event_time": to_sql_datetime(refund.refund_time),
            },
        )


# ============================================================
# 行为事件
# ============================================================
def allocate_funnel_counts(
    total: int,
    weights: dict[str, float],
) -> dict[str, int]:
    """按权重把总量分配到各行为，并强制漏斗逐级收窄。

    权重先做归一化，保证各阶段数量之和等于 total。
    （否则 VIEW 权重为 1.0 时会独占全部配额，总量翻倍。）

    随后强制漏斗逐级**严格**收窄：count(前) > count(后)。

    当 total 足够大（>= 10）时，先为四级阶梯预留 1/2/3/4 的下限，
    保证 BUY 至少有一条；剩余配额再按权重分配。

    注意：严格四级阶梯至少需要 10 个配额。当 total 很小时无法同时
    满足「严格递减」「BUY >= 1」与「总量守恒」，此时优先保证
    总量守恒与单调性。
    """
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        raise ValueError("行为权重之和必须大于 0")
    if total <= 0:
        return {name: 0 for name in weights}

    counts = {
        name: max(int(total * weight / weight_sum), 0)
        for name, weight in weights.items()
    }

    # 严格四级阶梯所需的最小配额：1 + 2 + 3 + 4 = 10
    strict_ladder_cost = sum(range(1, len(MAIN_FUNNEL) + 1))
    if total >= strict_ladder_cost:
        # 预留下限：从 VIEW 到底部逐级递减（VIEW 拿最大值）
        # MAIN_FUNNEL = [VIEW, CLICK, CART, BUY] -> 4, 3, 2, 1
        stage_count = len(MAIN_FUNNEL)
        ladder_min = {
            stage: stage_count - index for index, stage in enumerate(MAIN_FUNNEL)
        }
        remaining = total - strict_ladder_cost

        counts = dict(ladder_min)
        # 剩余配额按权重分配
        for stage in MAIN_FUNNEL:
            counts[stage] += int(remaining * weights.get(stage, 0.0) / weight_sum)
        counts[EVENT_FAVORITE] = max(
            int(remaining * weights.get(EVENT_FAVORITE, 0.0) / weight_sum), 0
        )

    # 从末级向前强制严格递减（BUY < CART < CLICK < VIEW）
    for i in range(len(MAIN_FUNNEL) - 1, 0, -1):
        upper, lower = MAIN_FUNNEL[i - 1], MAIN_FUNNEL[i]
        if counts.get(lower, 0) >= counts.get(upper, 0):
            counts[lower] = max(counts.get(upper, 0) - 1, 0)

    # FAVORITE 为旁支，必须在 VIEW 之下
    if counts.get(EVENT_FAVORITE, 0) >= counts.get(EVENT_VIEW, 0):
        counts[EVENT_FAVORITE] = max(counts.get(EVENT_VIEW, 0) - 1, 0)

    # 归一化取整后的余量补到 VIEW，使总量与 total 一致
    remainder = total - sum(counts.values())
    if remainder > 0:
        counts[EVENT_VIEW] = counts.get(EVENT_VIEW, 0) + remainder

    return counts


def _random_behavior_time(rng: random.Random, user: User) -> datetime:
    """在 [用户注册时间, 当前时间] 区间内随机取一个行为时间。

    必须夹取上界：否则「注册时间 + 最多 730 天」可能落到未来，
    导致 Flink 事件时间水位线无法推进、窗口不关闭。
    """
    now = now_cn()
    start = user.register_time
    if start >= now:
        # 注册时间异常（不应发生），退化为当前时间
        return now
    span = int((now - start).total_seconds())
    return (start + timedelta(seconds=rng.randint(1, max(span, 1)))).replace(microsecond=0)


def behavior_events(
    rng: random.Random,
    cfg: GenerateConfig,
    dataset: Dataset,
) -> Iterator[Event]:
    """行为事件：VIEW / CLICK / CART / FAVORITE / BUY。

    - user_id / product_id 均取自真实存在的用户与商品；
    - BUY 事件只针对真实下过单的商品，与 orders 表语义一致；
    - 漏斗逐级收窄；
    - 分区键 = user_id，保证同一用户行为分区内有序。
    """
    total = cfg.behavior_event_count
    if total <= 0 or not dataset.users or not dataset.products:
        return

    counts = allocate_funnel_counts(total, cfg.behavior_funnel)
    log.info("行为事件分布：%s", counts)

    purchased_pairs = sorted({(o.user_id, o.product_id) for o in dataset.orders})
    users_by_id = {u.user_id: u for u in dataset.users}
    products_by_id = {p.product_id: p for p in dataset.products}

    id_gen = EventIdGenerator(prefix=4)
    for event_type in BEHAVIOR_FUNNEL_ORDER:
        for _ in range(counts.get(event_type, 0)):
            if event_type == EVENT_BUY and purchased_pairs:
                user_id, product_id = rng.choice(purchased_pairs)
                user = users_by_id.get(user_id) or rng.choice(dataset.users)
            else:
                user = rng.choice(dataset.users)
                product = rng.choice(dataset.products)
                user_id, product_id = user.user_id, product.product_id

            behavior_value = {
                "event_id": id_gen.next(),
                "event_type": event_type,
                "user_id": user_id,
                "product_id": product_id,
                "device": rng.choices(
                    BEHAVIOR_DEVICES, weights=BEHAVIOR_DEVICE_WEIGHTS, k=1
                )[0],
                "province": user.province,
                # 行为时间：必须落在 [注册时间, 当前时间] 区间内
                #
                # !! 实测教训 !!
                #   早期实现直接写 register_time + randint(1, 730天)，
                #   由于注册时间本身可以是 730 天前，两者相加会**落到未来**
                #   （实测出现 2027-08-24 这类未来时间）。
                #   后果：Flink 以事件时间做滚动窗口，
                #   水位线永远追不上未来事件，窗口迟迟不关闭，
                #   下游 ADS 指标无输出。
                #   因此这里显式夹取到「当前时间」上限 ——
                #   与 orders 的 create_time 处理方式保持一致。
                "event_time": to_sql_datetime(_random_behavior_time(rng, user)),
            }
            # 冗余商品类目，便于下游按类目出流量指标（无需连维表）
            product = products_by_id.get(product_id)
            if product is not None:
                behavior_value["category_name"] = product.category_name

            yield Event(
                topic=TOPIC_BEHAVIOR,
                key=str(user_id),
                value=behavior_value,
            )


# ============================================================
# 汇总
# ============================================================
def build_events(
    cfg: GenerateConfig,
    dataset: Dataset,
    rng: random.Random,
    topics: list[str] | None = None,
    behavior_count: int | None = None,
) -> Iterator[Event]:
    """按 Topic 顺序产出事件（生成器）。

    event_id 前缀与 SPRINT_0.md 示例保持一致：
        订单 1xxxx / 支付 2xxxx / 退款 3xxxx / 行为 4xxxx

    Args:
        topics: 只产出指定 Topic；None 表示全部。
        behavior_count: 覆盖行为事件条数（用于持续发送模式）。
    """
    selected = set(topics) if topics else set(ALL_TOPICS)

    if TOPIC_ORDER in selected:
        yield from order_events(dataset.orders, dataset.products)
    if TOPIC_PAYMENT in selected:
        yield from payment_events(dataset.payments)
    if TOPIC_REFUND in selected:
        yield from refund_events(dataset.refunds)
    if TOPIC_BEHAVIOR in selected:
        if behavior_count is not None:
            from dataclasses import replace

            cfg = replace(cfg, behavior_event_count=behavior_count)
        yield from behavior_events(rng, cfg, dataset)
