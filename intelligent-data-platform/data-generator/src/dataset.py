"""业务数据模型与生成器。

核心约束（依据 docs/sprint/SPRINT_0.md 第 13 节）：
    订单 user_id    必须来自 user
    订单 product_id 必须来自 product
    payment.order_id 必须来自 orders
    refund.order_id  必须来自 orders
    product.price × quantity ≈ order.amount

同时满足时间因果：
    register_time <= order.create_time <= order.pay_time <= refund.refund_time
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from faker import Faker

from .common import CN_TZ, get_logger, now_cn, random_past_time
from .config import GenerateConfig

log = get_logger("dataset")

# ------------------------------------------------------------
# ID 基数：与 docs/development-environment.md 约定保持一致
# ------------------------------------------------------------
USER_ID_BASE = 1001
PRODUCT_ID_BASE = 2001
ORDER_ID_BASE = 10001
PAYMENT_ID_BASE = 30001
REFUND_ID_BASE = 40001

# ------------------------------------------------------------
# 业务枚举
# ------------------------------------------------------------
USER_LEVELS = ["BRONZE", "SILVER", "GOLD", "PLATINUM", "DIAMOND"]
USER_LEVEL_WEIGHTS = [40, 30, 18, 9, 3]

GENDERS = [0, 1, 2]
GENDER_WEIGHTS = [15, 42, 43]

PAYMENT_METHODS = ["ALIPAY", "WECHAT", "UNIONPAY", "CREDIT_CARD", "COD"]
PAYMENT_METHOD_WEIGHTS = [40, 40, 8, 7, 5]

DEVICES = ["PC", "APP", "H5", "MINI_PROGRAM"]
DEVICE_WEIGHTS = [25, 45, 15, 15]

ORDER_STATUS_CREATED = "CREATED"
ORDER_STATUS_PAID = "PAID"
ORDER_STATUS_CANCELLED = "CANCELLED"
ORDER_STATUS_REFUNDED = "REFUNDED"

REFUND_STATUS_CREATED = "CREATED"

REFUND_REASONS = [
    "七天无理由退货",
    "商品质量问题",
    "商品与描述不符",
    "发错货",
    "物流损坏",
    "不想要了",
    "尺寸不合适",
    "重复下单",
]

# ------------------------------------------------------------
# 商品类目：(category_id, category_name, brands, price_range)
# ------------------------------------------------------------
CATEGORIES: list[tuple[int, str, list[str], tuple[int, int]]] = [
    (1, "手机数码", ["华为", "小米", "Apple", "OPPO", "vivo", "荣耀", "一加", "realme"], (899, 8999)),
    (2, "电脑办公", ["联想", "戴尔", "惠普", "华硕", "Apple", "宏碁", "机械革命"], (1999, 15999)),
    (3, "家用电器", ["美的", "格力", "海尔", "西门子", "松下", "TCL", "海信"], (299, 9999)),
    (4, "服饰鞋包", ["优衣库", "耐克", "阿迪达斯", "李宁", "安踏", "森马", "海澜之家"], (59, 1299)),
    (5, "美妆个护", ["欧莱雅", "兰蔻", "雅诗兰黛", "珀莱雅", "薇诺娜", "花西子"], (39, 1599)),
    (6, "食品生鲜", ["三只松鼠", "良品铺子", "伊利", "蒙牛", "百草味", "洽洽"], (19, 399)),
    (7, "图书文娱", ["中信出版社", "人民邮电", "机械工业", "读客", "磨铁"], (15, 299)),
    (8, "运动户外", ["迪卡侬", "耐克", "阿迪达斯", "李宁", "凯乐石", "骆驼"], (49, 2599)),
    (9, "家居家装", ["宜家", "林氏木业", "顾家家居", "慕思", "全友"], (99, 8999)),
    (10, "母婴玩具", ["好孩子", "babycare", "乐高", "费雪", "巴拉巴拉"], (29, 1999)),
]

PRODUCT_NAME_SUFFIX = [
    "标准版", "旗舰版", "青春版", "Pro", "Plus", "Max", "尊享版", "经典款",
    "2026新款", "升级版", "专业版", "轻薄款", "限量款", "套装",
]

PROVINCE_CITIES: dict[str, list[str]] = {
    "Zhejiang": ["Hangzhou", "Ningbo", "Wenzhou", "Jiaxing", "Yiwu"],
    "Jiangsu": ["Nanjing", "Suzhou", "Wuxi", "Changzhou", "Nantong"],
    "Guangdong": ["Guangzhou", "Shenzhen", "Dongguan", "Foshan", "Zhuhai"],
    "Shandong": ["Jinan", "Qingdao", "Yantai", "Weifang", "Linyi"],
    "Henan": ["Zhengzhou", "Luoyang", "Kaifeng", "Xinxiang"],
    "Sichuan": ["Chengdu", "Mianyang", "Deyang", "Yibin"],
    "Hubei": ["Wuhan", "Yichang", "Xiangyang", "Huangshi"],
    "Hunan": ["Changsha", "Zhuzhou", "Xiangtan", "Hengyang"],
    "Fujian": ["Fuzhou", "Xiamen", "Quanzhou", "Zhangzhou"],
    "Beijing": ["Beijing"],
    "Shanghai": ["Shanghai"],
    "Tianjin": ["Tianjin"],
    "Chongqing": ["Chongqing"],
    "Shaanxi": ["Xi'an", "Xianyang", "Baoji"],
    "Liaoning": ["Shenyang", "Dalian", "Anshan"],
    "Hebei": ["Shijiazhuang", "Tangshan", "Baoding"],
    "Anhui": ["Hefei", "Wuhu", "Bengbu"],
    "Jiangxi": ["Nanchang", "Ganzhou", "Jiujiang"],
    "Guangxi": ["Nanning", "Liuzhou", "Guilin"],
    "Yunnan": ["Kunming", "Qujing", "Dali"],
}


def _q2(value: Decimal) -> Decimal:
    """保留 2 位小数，四舍五入。"""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ============================================================
# 数据模型
# ============================================================
@dataclass
class User:
    user_id: int
    username: str
    gender: int
    age: int
    province: str
    city: str
    user_level: str
    register_time: datetime
    update_time: datetime


@dataclass
class Product:
    product_id: int
    product_name: str
    category_id: int
    category_name: str
    brand: str
    price: Decimal
    cost: Decimal
    status: int
    create_time: datetime
    update_time: datetime


@dataclass
class Order:
    order_id: int
    user_id: int
    product_id: int
    quantity: int
    amount: Decimal
    status: str
    create_time: datetime
    pay_time: datetime | None
    update_time: datetime
    # --- 以下字段仅用于生成 Kafka 事件，不写入 MySQL ---
    payment_method: str | None = None
    device: str | None = None


@dataclass
class Payment:
    payment_id: int
    order_id: int
    user_id: int
    amount: Decimal
    payment_method: str
    payment_status: str
    payment_time: datetime


@dataclass
class Refund:
    refund_id: int
    order_id: int
    user_id: int
    refund_amount: Decimal
    refund_reason: str
    refund_status: str
    refund_time: datetime


@dataclass
class Dataset:
    """一次生成的完整业务数据集。"""

    users: list[User]
    products: list[Product]
    orders: list[Order]
    payments: list[Payment]
    refunds: list[Refund]
    seed: int

    def summary(self) -> dict[str, int]:
        return {
            "users": len(self.users),
            "products": len(self.products),
            "orders": len(self.orders),
            "payments": len(self.payments),
            "refunds": len(self.refunds),
        }


# ============================================================
# 生成逻辑
# ============================================================
def generate_users(rng: random.Random, faker: Faker, count: int) -> list[User]:
    """生成用户主数据。"""
    log.info("生成 %d 个用户 ...", count)
    users: list[User] = []
    provinces = list(PROVINCE_CITIES.keys())

    for i in range(count):
        user_id = USER_ID_BASE + i
        province = rng.choice(provinces)
        city = rng.choice(PROVINCE_CITIES[province])
        register_time = random_past_time(rng, max_days_ago=730)

        users.append(
            User(
                user_id=user_id,
                username=faker.user_name() + str(rng.randint(1, 9999)),
                gender=rng.choices(GENDERS, weights=GENDER_WEIGHTS, k=1)[0],
                age=rng.randint(18, 65),
                province=province,
                city=city,
                user_level=rng.choices(USER_LEVELS, weights=USER_LEVEL_WEIGHTS, k=1)[0],
                register_time=register_time,
                # 用户信息可能在注册后被更新过
                update_time=register_time + timedelta(days=rng.randint(0, 60)),
            )
        )
    return users


def generate_products(rng: random.Random, count: int) -> list[Product]:
    """生成商品主数据。

    保证 price > cost，毛利率 15% ~ 60%。
    """
    log.info("生成 %d 个商品 ...", count)
    products: list[Product] = []

    for i in range(count):
        product_id = PRODUCT_ID_BASE + i
        category_id, category_name, brands, (low, high) = rng.choice(CATEGORIES)
        brand = rng.choice(brands)

        # 价格在对数尺度上分布，避免高价商品过度集中
        price = Decimal(rng.randint(low, high)) + Decimal(rng.choice([0, 0, 0, 50, 99]))
        margin = Decimal(str(round(rng.uniform(0.15, 0.60), 4)))
        cost = _q2(price * (Decimal(1) - margin))

        create_time = random_past_time(rng, max_days_ago=900)
        products.append(
            Product(
                product_id=product_id,
                product_name=f"{brand}{category_name}{rng.choice(PRODUCT_NAME_SUFFIX)}"
                f"-{rng.randint(100, 999)}",
                category_id=category_id,
                category_name=category_name,
                brand=brand,
                price=_q2(price),
                cost=cost,
                # 95% 在架
                status=1 if rng.random() < 0.95 else 0,
                create_time=create_time,
                update_time=create_time + timedelta(days=rng.randint(0, 90)),
            )
        )
    return products


def generate_orders(
    rng: random.Random,
    cfg: GenerateConfig,
    users: list[User],
    products: list[Product],
) -> list[Order]:
    """生成订单。

    - user_id 取自 users  （外键保证）
    - product_id 取自 products（外键保证）
    - amount = price × quantity，按 discount_ratio 概率打折
    - create_time 不早于用户注册时间
    """
    log.info("生成 %d 个订单 ...", cfg.order_count)
    orders: list[Order] = []
    now = now_cn()

    for i in range(cfg.order_count):
        order_id = ORDER_ID_BASE + i
        user = rng.choice(users)
        product = rng.choice(products)
        quantity = rng.randint(1, cfg.max_quantity)

        amount = _q2(product.price * quantity)
        if rng.random() < cfg.discount_ratio:
            # 模拟优惠：9 折 ~ 9.8 折
            discount = Decimal(str(round(rng.uniform(0.90, 0.98), 4)))
            amount = _q2(amount * discount)

        # 下单时间必须晚于注册时间，且不晚于当前时间
        earliest = max(user.register_time, product.create_time)
        span = max(int((now - earliest).total_seconds()), 1)
        create_time = (earliest + timedelta(seconds=rng.randint(0, span))).replace(microsecond=0)

        paid = rng.random() < cfg.pay_ratio
        pay_time = None
        status = ORDER_STATUS_CREATED
        if paid:
            # 支付发生在下单后 1 分钟 ~ 2 天内，且不超过当前时间
            delta = timedelta(seconds=rng.randint(60, 2 * 24 * 3600))
            candidate = create_time + delta
            if candidate <= now:
                pay_time = candidate
                status = ORDER_STATUS_PAID
            else:
                # 时间越界则视为未支付，保持因果一致
                paid = False
        if not paid and rng.random() < 0.2:
            status = ORDER_STATUS_CANCELLED

        orders.append(
            Order(
                order_id=order_id,
                user_id=user.user_id,
                product_id=product.product_id,
                quantity=quantity,
                amount=amount,
                status=status,
                create_time=create_time,
                pay_time=pay_time,
                update_time=pay_time or create_time,
                payment_method=rng.choices(
                    PAYMENT_METHODS, weights=PAYMENT_METHOD_WEIGHTS, k=1
                )[0],
                device=rng.choices(DEVICES, weights=DEVICE_WEIGHTS, k=1)[0],
            )
        )
    return orders


def generate_payments(rng: random.Random, orders: list[Order]) -> list[Payment]:
    """生成支付记录。

    只有 status=PAID 的订单才有支付记录，保证
    payment.order_id 一定存在于 orders 且业务语义正确。
    """
    paid_orders = [o for o in orders if o.status == ORDER_STATUS_PAID and o.pay_time is not None]
    log.info("生成 %d 条支付记录（对应已支付订单）...", len(paid_orders))

    payments: list[Payment] = []
    for i, order in enumerate(paid_orders):
        # 少量支付失败记录，模拟真实场景
        failed = rng.random() < 0.02
        payments.append(
            Payment(
                payment_id=PAYMENT_ID_BASE + i,
                order_id=order.order_id,
                user_id=order.user_id,
                amount=order.amount,
                payment_method=order.payment_method or "ALIPAY",
                payment_status="FAILED" if failed else "SUCCESS",
                payment_time=order.pay_time,  # type: ignore[arg-type]
            )
        )
    return payments


def generate_refunds(
    rng: random.Random,
    cfg: GenerateConfig,
    orders: list[Order],
    payments: list[Payment],
) -> list[Refund]:
    """生成退款记录。

    - 只对支付成功的订单退款
    - refund_amount <= order.amount
    - refund_time >= payment_time
    """
    successful = [
        p for p in payments if p.payment_status == "SUCCESS"
    ]
    order_by_id = {o.order_id: o for o in orders}
    now = now_cn()

    refunds: list[Refund] = []
    for payment in successful:
        if rng.random() >= cfg.refund_ratio:
            continue

        order = order_by_id.get(payment.order_id)
        if order is None:
            continue

        # 全额或部分退款
        if rng.random() < 0.7:
            refund_amount = order.amount
        else:
            ratio = Decimal(str(round(rng.uniform(0.1, 0.9), 2)))
            refund_amount = _q2(order.amount * ratio)

        delta = timedelta(seconds=rng.randint(3600, 30 * 24 * 3600))
        refund_time = payment.payment_time + delta
        if refund_time > now:
            refund_time = now

        refunds.append(
            Refund(
                refund_id=REFUND_ID_BASE + len(refunds),
                order_id=order.order_id,
                user_id=order.user_id,
                refund_amount=refund_amount,
                refund_reason=rng.choice(REFUND_REASONS),
                refund_status=REFUND_STATUS_CREATED,
                refund_time=refund_time.replace(microsecond=0),
            )
        )

    log.info("生成 %d 条退款记录（退款率 %.1f%%）...", len(refunds), cfg.refund_ratio * 100)

    # 退款后同步订单状态
    refunded_order_ids = {r.order_id for r in refunds}
    for order in orders:
        if order.order_id in refunded_order_ids:
            order.status = ORDER_STATUS_REFUNDED

    return refunds


def build_dataset(cfg: GenerateConfig) -> Dataset:
    """构建完整数据集（确定性：同一种子得到完全相同的结果）。"""
    rng = random.Random(cfg.random_seed)
    # Faker 也使用同一随机源，保证可复现
    faker = Faker("zh_CN")
    faker.seed_instance(cfg.random_seed)

    log.info("随机种子：%d", cfg.random_seed)

    users = generate_users(rng, faker, cfg.user_count)
    products = generate_products(rng, cfg.product_count)
    orders = generate_orders(rng, cfg, users, products)
    payments = generate_payments(rng, orders)
    refunds = generate_refunds(rng, cfg, orders, payments)

    return Dataset(
        users=users,
        products=products,
        orders=orders,
        payments=payments,
        refunds=refunds,
        seed=cfg.random_seed,
    )


# ============================================================
# 快照持久化
#
# 作用：让 generate_events 基于「真实写入 MySQL 的订单/支付」
#       产生 Kafka 事件，而不是重新随机生成一套不一致的数据。
# ============================================================
SNAPSHOT_VERSION = 1


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"无法序列化类型 {type(value)!r}")


def snapshot_path() -> Path:
    return Path(__file__).resolve().parents[1] / "state" / "dataset_snapshot.json"


def save_snapshot(dataset: Dataset, path: Path | None = None) -> Path:
    """保存数据集快照。"""
    target = path or snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": SNAPSHOT_VERSION,
        "generated_at": now_cn().isoformat(),
        "seed": dataset.seed,
        "users": [asdict(u) for u in dataset.users],
        "products": [asdict(p) for p in dataset.products],
        "orders": [asdict(o) for o in dataset.orders],
        "payments": [asdict(p) for p in dataset.payments],
        "refunds": [asdict(r) for r in dataset.refunds],
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, default=_encode, indent=2),
        encoding="utf-8",
    )
    log.info("数据集快照已保存：%s", target)
    return target


def load_snapshot(path: Path | None = None) -> Dataset:
    """读取数据集快照。"""
    source = path or snapshot_path()
    if not source.is_file():
        raise FileNotFoundError(
            f"找不到数据集快照 {source}。\n"
            "请先执行：python -m src.generate_mysql_data\n"
            "这样 Kafka 事件才能与 MySQL 中的真实数据保持一致。"
        )

    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("version") != SNAPSHOT_VERSION:
        raise RuntimeError(
            f"快照版本不匹配（期望 {SNAPSHOT_VERSION}，实际 {payload.get('version')}）。\n"
            "请重新执行：python -m src.generate_mysql_data"
        )

    def _dt(value: str | None) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)

    users = [
        User(
            **{
                **u,
                "register_time": _dt(u["register_time"]),
                "update_time": _dt(u["update_time"]),
            }
        )
        for u in payload["users"]
    ]
    products = [
        Product(
            **{
                **p,
                "price": Decimal(p["price"]),
                "cost": Decimal(p["cost"]),
                "create_time": _dt(p["create_time"]),
                "update_time": _dt(p["update_time"]),
            }
        )
        for p in payload["products"]
    ]
    orders = [
        Order(
            **{
                **o,
                "amount": Decimal(o["amount"]),
                "create_time": _dt(o["create_time"]),
                "pay_time": _dt(o["pay_time"]),
                "update_time": _dt(o["update_time"]),
            }
        )
        for o in payload["orders"]
    ]
    payments = [
        Payment(
            **{
                **p,
                "amount": Decimal(p["amount"]),
                "payment_time": _dt(p["payment_time"]),
            }
        )
        for p in payload["payments"]
    ]
    refunds = [
        Refund(
            **{
                **r,
                "refund_amount": Decimal(r["refund_amount"]),
                "refund_time": _dt(r["refund_time"]),
            }
        )
        for r in payload["refunds"]
    ]

    log.info("已加载数据集快照：%s", source)
    return Dataset(
        users=users,
        products=products,
        orders=orders,
        payments=payments,
        refunds=refunds,
        seed=payload["seed"],
    )
