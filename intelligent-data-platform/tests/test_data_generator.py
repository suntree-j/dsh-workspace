"""数据生成器单元测试。

这些测试**不依赖任何外部服务**，可直接运行：
    python -m pytest -m unit

验证的是「业务关系是否合理」，而不是「文件是否存在」：
    订单 user_id    必须来自 user
    订单 product_id 必须来自 product
    payment.order_id 必须来自 orders
    refund.order_id  必须来自 orders
    product.price × quantity ≈ order.amount
    行为漏斗必须逐级收窄
"""

from __future__ import annotations

import random
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

# 让 tests 能 import 到 data-generator/src
_DATA_GENERATOR = Path(__file__).resolve().parents[1] / "data-generator"
if str(_DATA_GENERATOR) not in sys.path:
    sys.path.insert(0, str(_DATA_GENERATOR))

from src.config import GenerateConfig  # noqa: E402
from src.dataset import (  # noqa: E402
    ORDER_STATUS_PAID,
    ORDER_STATUS_REFUNDED,
    build_dataset,
)
from src.kafka_events import (  # noqa: E402
    EVENT_BUY,
    EVENT_CART,
    EVENT_CLICK,
    EVENT_FAVORITE,
    EVENT_ORDER_CREATED,
    EVENT_PAYMENT_FAILED,
    EVENT_PAYMENT_SUCCESS,
    EVENT_REFUND_CREATED,
    EVENT_VIEW,
    TOPIC_BEHAVIOR,
    TOPIC_ORDER,
    TOPIC_PAYMENT,
    TOPIC_REFUND,
    allocate_funnel_counts,
    build_events,
)

pytestmark = pytest.mark.unit


# ------------------------------------------------------------
# 夹具：小规模数据集，保证测试快速
# ------------------------------------------------------------
@pytest.fixture(scope="module")
def gen_config() -> GenerateConfig:
    return replace(
        GenerateConfig(),
        user_count=120,
        product_count=60,
        order_count=400,
        behavior_event_count=1500,
        random_seed=20260926,
    )


@pytest.fixture(scope="module")
def dataset(gen_config: GenerateConfig):
    return build_dataset(gen_config)


# ============================================================
# 规模
# ============================================================
def test_dataset_meets_required_scale():
    """默认配置必须满足 Sprint 0 的规模要求。"""
    cfg = GenerateConfig()
    assert cfg.user_count >= 1000, "用户需 1000+"
    assert cfg.product_count >= 500, "商品需 500+"
    assert cfg.order_count >= 5000, "订单需 5000+"
    assert cfg.behavior_event_count > 0


def test_dataset_sizes(dataset, gen_config):
    assert len(dataset.users) == gen_config.user_count
    assert len(dataset.products) == gen_config.product_count
    assert len(dataset.orders) == gen_config.order_count
    assert len(dataset.payments) > 0
    assert len(dataset.refunds) > 0, "应生成少量退款"


# ============================================================
# 主键唯一
# ============================================================
def test_primary_keys_unique(dataset):
    for name, rows, attr in [
        ("user", dataset.users, "user_id"),
        ("product", dataset.products, "product_id"),
        ("orders", dataset.orders, "order_id"),
        ("payment", dataset.payments, "payment_id"),
        ("refund", dataset.refunds, "refund_id"),
    ]:
        ids = [getattr(r, attr) for r in rows]
        assert len(ids) == len(set(ids)), f"{name} 主键重复"


# ============================================================
# 外键关系（核心业务约束）
# ============================================================
def test_order_user_fk(dataset):
    """order.user_id 必须存在于 user。"""
    user_ids = {u.user_id for u in dataset.users}
    assert all(o.user_id in user_ids for o in dataset.orders)


def test_order_product_fk(dataset):
    """order.product_id 必须存在于 product。"""
    product_ids = {p.product_id for p in dataset.products}
    assert all(o.product_id in product_ids for o in dataset.orders)


def test_payment_order_fk(dataset):
    """payment.order_id 必须存在于 orders。"""
    order_ids = {o.order_id for o in dataset.orders}
    assert all(p.order_id in order_ids for p in dataset.payments)


def test_refund_order_fk(dataset):
    """refund.order_id 必须存在于 orders。"""
    order_ids = {o.order_id for o in dataset.orders}
    assert all(r.order_id in order_ids for r in dataset.refunds)


def test_payment_user_matches_order(dataset):
    """payment.user_id 必须与对应订单的 user_id 一致。"""
    order_user = {o.order_id: o.user_id for o in dataset.orders}
    assert all(p.user_id == order_user[p.order_id] for p in dataset.payments)


def test_refund_amount_not_exceed_order(dataset):
    """退款金额不得超过订单金额。"""
    order_amount = {o.order_id: o.amount for o in dataset.orders}
    for refund in dataset.refunds:
        assert refund.refund_amount <= order_amount[refund.order_id]


# ============================================================
# 金额逻辑
# ============================================================
def test_order_amount_matches_price_times_quantity(dataset):
    """product.price × quantity ≈ order.amount（允许优惠折扣）。"""
    price = {p.product_id: p.price for p in dataset.products}
    for order in dataset.orders:
        expected = price[order.product_id] * order.quantity
        # 允许最多 10% 的优惠
        assert order.amount <= expected, f"订单 {order.order_id} 金额高于原价"
        assert order.amount >= expected * Decimal("0.89"), (
            f"订单 {order.order_id} 金额偏离过大"
        )


def test_product_price_above_cost(dataset):
    """商品售价必须高于成本价。"""
    assert all(p.price > p.cost for p in dataset.products)


# ============================================================
# 时间因果
# ============================================================
def test_order_after_user_register(dataset):
    """下单时间不得早于用户注册时间。"""
    users = {u.user_id: u for u in dataset.users}
    for order in dataset.orders:
        assert order.create_time >= users[order.user_id].register_time


def test_pay_after_order(dataset):
    """支付时间不得早于下单时间。"""
    for order in dataset.orders:
        if order.pay_time is not None:
            assert order.pay_time >= order.create_time


def test_refund_after_payment(dataset):
    """退款时间不得早于支付时间。"""
    payments = {p.order_id: p for p in dataset.payments}
    for refund in dataset.refunds:
        payment = payments.get(refund.order_id)
        assert payment is not None, "退款必须对应一笔支付"
        assert refund.refund_time >= payment.payment_time


def test_paid_orders_have_payment_time(dataset):
    """状态为 PAID / REFUNDED 的订单必须有支付时间。"""
    for order in dataset.orders:
        if order.status in (ORDER_STATUS_PAID, ORDER_STATUS_REFUNDED):
            assert order.pay_time is not None


# ============================================================
# 确定性（同一 seed 产生相同结果）
# ============================================================
def test_generation_is_deterministic(gen_config):
    a = build_dataset(gen_config)
    b = build_dataset(gen_config)
    assert [u.user_id for u in a.users] == [u.user_id for u in b.users]
    assert [o.amount for o in a.orders] == [o.amount for o in b.orders]
    assert [p.payment_id for p in a.payments] == [p.payment_id for p in b.payments]


# ============================================================
# Kafka 事件
# ============================================================
def test_order_event_matches_orders(dataset, gen_config):
    """order_event 必须与 orders 表一一对应。"""
    events = list(build_events(gen_config, dataset, random.Random(1), topics=[TOPIC_ORDER]))
    assert len(events) == len(dataset.orders)

    by_order = {o.order_id: o for o in dataset.orders}
    for event in events:
        value = event.value
        assert value["event_type"] == EVENT_ORDER_CREATED
        order = by_order[value["order_id"]]
        assert value["user_id"] == order.user_id
        assert value["product_id"] == order.product_id
        assert value["quantity"] == order.quantity
        assert value["amount"] == float(order.amount)
        # 分区键必须等于 order_id
        assert event.key == str(order.order_id)


def test_payment_event_types(dataset, gen_config):
    events = list(build_events(gen_config, dataset, random.Random(1), topics=[TOPIC_PAYMENT]))
    assert len(events) == len(dataset.payments)

    allowed = {EVENT_PAYMENT_SUCCESS, EVENT_PAYMENT_FAILED}
    assert all(e.value["event_type"] in allowed for e in events)
    assert all(e.value["payment_method"] for e in events)
    # 分区键必须等于 order_id
    assert all(e.key == str(e.value["order_id"]) for e in events)


def test_refund_event_matches_refunds(dataset, gen_config):
    events = list(build_events(gen_config, dataset, random.Random(1), topics=[TOPIC_REFUND]))
    assert len(events) == len(dataset.refunds)
    assert all(e.value["event_type"] == EVENT_REFUND_CREATED for e in events)
    assert all(e.key == str(e.value["order_id"]) for e in events)


def test_behavior_events_funnel_narrows(dataset, gen_config):
    """行为漏斗必须逐级收窄：VIEW > CLICK > CART > BUY。"""
    events = list(build_events(gen_config, dataset, random.Random(1), topics=[TOPIC_BEHAVIOR]))

    counts: dict[str, int] = {}
    for event in events:
        key = event.value["event_type"]
        counts[key] = counts.get(key, 0) + 1

    for stage in (EVENT_VIEW, EVENT_CLICK, EVENT_CART, EVENT_FAVORITE, EVENT_BUY):
        assert stage in counts, f"缺少行为事件类型 {stage}"

    assert counts[EVENT_VIEW] > counts[EVENT_CLICK], "VIEW 应多于 CLICK"
    assert counts[EVENT_CLICK] > counts[EVENT_CART], "CLICK 应多于 CART"
    assert counts[EVENT_CART] > counts[EVENT_BUY], "CART 应多于 BUY"
    assert counts[EVENT_VIEW] > counts[EVENT_FAVORITE], "FAVORITE 应少于 VIEW"


def test_behavior_events_reference_real_entities(dataset, gen_config):
    """行为事件的 user_id / product_id 必须真实存在。"""
    events = list(build_events(gen_config, dataset, random.Random(1), topics=[TOPIC_BEHAVIOR]))
    user_ids = {u.user_id for u in dataset.users}
    product_ids = {p.product_id for p in dataset.products}

    for event in events:
        assert event.value["user_id"] in user_ids
        assert event.value["product_id"] in product_ids
        assert event.value["device"] in {"PC", "APP", "H5", "MINI_PROGRAM"}
        # 分区键必须等于 user_id
        assert event.key == str(event.value["user_id"])


def test_behavior_event_count_respected(dataset, gen_config):
    events = list(build_events(gen_config, dataset, random.Random(1), topics=[TOPIC_BEHAVIOR]))
    assert len(events) == gen_config.behavior_event_count


def test_event_ids_unique_across_topics(dataset, gen_config):
    events = list(build_events(gen_config, dataset, random.Random(1)))
    ids = [e.value["event_id"] for e in events]
    assert len(ids) == len(set(ids)), "event_id 必须全局唯一"


def test_allocate_funnel_counts_is_monotonic():
    """漏斗分配必须单调收窄，且总量守恒。

    严格四级阶梯至少需要 10 个配额（1+2+3+4），
    因此只在 total >= 10 时断言严格递减。
    """
    weights = {"VIEW": 1.0, "CLICK": 0.55, "CART": 0.2, "FAVORITE": 0.1, "BUY": 0.06}

    for total in (10, 100, 1000, 20000):
        counts = allocate_funnel_counts(total, weights)
        assert counts["VIEW"] > counts["CLICK"] > counts["CART"] > counts["BUY"], (
            f"total={total} 时漏斗未收窄：{counts}"
        )
        assert counts["VIEW"] > counts["FAVORITE"]
        assert counts["BUY"] >= 1
        assert sum(counts.values()) == total, (
            f"total={total} 时配额之和不等于总量：{counts}"
        )

    # 配额极小时：仍须单调不减，且总量守恒
    for total in (1, 5, 9):
        counts = allocate_funnel_counts(total, weights)
        assert sum(counts.values()) == total, f"total={total} 违反总量守恒：{counts}"
        assert counts["VIEW"] >= counts["CLICK"] >= counts["CART"] >= counts["BUY"], (
            f"total={total} 时漏斗非单调：{counts}"
        )
