"""生成 MySQL 业务数据。

用法：
    python -m src.generate_mysql_data
    python -m src.generate_mysql_data --reset          # 先清空再生成
    python -m src.generate_mysql_data --users 2000 --orders 10000
    python -m src.generate_mysql_data --seed 42

生成内容（依据 docs/sprint/SPRINT_0.md 第 12 节）：
    user     1000+
    product   500+
    orders   5000+
    payment  5000+
    refund   少量（默认约 5% 的已支付订单）

完成后会把数据集快照写入 state/dataset_snapshot.json，
供 src.generate_events 生成与 MySQL 一致的 Kafka 事件。
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from .common import get_logger, to_sql_datetime
from .config import GenerateConfig, load_config
from .dataset import (
    Dataset,
    Order,
    Payment,
    Product,
    Refund,
    User,
    build_dataset,
    save_snapshot,
)
from .db import MySQLClient

log = get_logger("generate_mysql_data")

# 清空顺序：先子表后父表（存在外键约束）
TRUNCATE_ORDER = ["refund", "payment", "orders", "product", "user"]


# ------------------------------------------------------------
# 行映射：dataclass -> MySQL 列/值
# ------------------------------------------------------------
USER_COLUMNS = [
    "user_id", "username", "gender", "age", "province",
    "city", "user_level", "register_time", "update_time",
]

PRODUCT_COLUMNS = [
    "product_id", "product_name", "category_id", "category_name", "brand",
    "price", "cost", "status", "create_time", "update_time",
]

ORDER_COLUMNS = [
    "order_id", "user_id", "product_id", "quantity", "amount",
    "status", "create_time", "pay_time", "update_time",
]

PAYMENT_COLUMNS = [
    "payment_id", "order_id", "user_id", "amount",
    "payment_method", "payment_status", "payment_time",
]

REFUND_COLUMNS = [
    "refund_id", "order_id", "user_id", "refund_amount",
    "refund_reason", "refund_status", "refund_time",
]


def _user_rows(users: list[User]):
    for u in users:
        yield (
            u.user_id, u.username, u.gender, u.age, u.province,
            u.city, u.user_level, to_sql_datetime(u.register_time),
            to_sql_datetime(u.update_time),
        )


def _product_rows(products: list[Product]):
    for p in products:
        yield (
            p.product_id, p.product_name, p.category_id, p.category_name, p.brand,
            p.price, p.cost, p.status, to_sql_datetime(p.create_time),
            to_sql_datetime(p.update_time),
        )


def _order_rows(orders: list[Order]):
    for o in orders:
        yield (
            o.order_id, o.user_id, o.product_id, o.quantity, o.amount,
            o.status, to_sql_datetime(o.create_time),
            to_sql_datetime(o.pay_time) if o.pay_time else None,
            to_sql_datetime(o.update_time),
        )


def _payment_rows(payments: list[Payment]):
    for p in payments:
        yield (
            p.payment_id, p.order_id, p.user_id, p.amount,
            p.payment_method, p.payment_status, to_sql_datetime(p.payment_time),
        )


def _refund_rows(refunds: list[Refund]):
    for r in refunds:
        yield (
            r.refund_id, r.order_id, r.user_id, r.refund_amount,
            r.refund_reason, r.refund_status, to_sql_datetime(r.refund_time),
        )


# ------------------------------------------------------------
# 写入
# ------------------------------------------------------------
def write_dataset(client: MySQLClient, dataset: Dataset, batch_size: int) -> dict[str, int]:
    """按依赖顺序写入全部表。"""
    written: dict[str, int] = {}

    log.info("写入 product ...")
    written["product"] = client.insert_many(
        "product", PRODUCT_COLUMNS, _product_rows(dataset.products), batch_size
    )

    log.info("写入 user ...")
    written["user"] = client.insert_many(
        "user", USER_COLUMNS, _user_rows(dataset.users), batch_size
    )

    log.info("写入 orders ...")
    written["orders"] = client.insert_many(
        "orders", ORDER_COLUMNS, _order_rows(dataset.orders), batch_size
    )

    log.info("写入 payment ...")
    written["payment"] = client.insert_many(
        "payment", PAYMENT_COLUMNS, _payment_rows(dataset.payments), batch_size
    )

    log.info("写入 refund ...")
    written["refund"] = client.insert_many(
        "refund", REFUND_COLUMNS, _refund_rows(dataset.refunds), batch_size
    )

    return written


# ------------------------------------------------------------
# 写入后校验（真正连库查询，不是只检查文件存在）
# ------------------------------------------------------------
def verify(client: MySQLClient) -> None:
    """在数据库层面校验业务关系，失败即抛错。"""
    log.info("校验业务关系 ...")

    checks: list[tuple[str, str, int]] = [
        (
            "order.user_id 全部存在于 user",
            """
            SELECT COUNT(*)
              FROM orders o
              LEFT JOIN user u ON o.user_id = u.user_id
             WHERE u.user_id IS NULL
            """,
            0,
        ),
        (
            "order.product_id 全部存在于 product",
            """
            SELECT COUNT(*)
              FROM orders o
              LEFT JOIN product p ON o.product_id = p.product_id
             WHERE p.product_id IS NULL
            """,
            0,
        ),
        (
            "payment.order_id 全部存在于 orders",
            """
            SELECT COUNT(*)
              FROM payment pm
              LEFT JOIN orders o ON pm.order_id = o.order_id
             WHERE o.order_id IS NULL
            """,
            0,
        ),
        (
            "refund.order_id 全部存在于 orders",
            """
            SELECT COUNT(*)
              FROM refund r
              LEFT JOIN orders o ON r.order_id = o.order_id
             WHERE o.order_id IS NULL
            """,
            0,
        ),
        (
            "payment 金额与订单金额一致的数量",
            """
            SELECT COUNT(*)
              FROM payment pm
              JOIN orders o ON pm.order_id = o.order_id
             WHERE pm.amount = o.amount
            """,
            -1,  # -1 表示只做展示，不参与断言
        ),
    ]

    failed = False
    for name, sql, expected in checks:
        value = int(client.fetch_scalar(sql) or 0)
        if expected == -1:
            log.info("  [统计] %s：%d", name, value)
        elif value == expected:
            log.info("  [OK]   %s", name)
        else:
            log.error("  [FAIL] %s（期望 %d，实际 %d）", name, expected, value)
            failed = True

    # 金额一致性抽样：price × quantity 与 amount 的偏差
    sample = client.fetch_all(
        """
        SELECT o.order_id, o.amount, p.price, o.quantity
          FROM orders o
          JOIN product p ON o.product_id = p.product_id
         ORDER BY o.order_id
         LIMIT 200
        """
    )
    mismatched = 0
    for _order_id, amount, price, quantity in sample:
        expected_amount = Decimal(price) * int(quantity)
        # 允许优惠造成的偏差（最低 9 折）
        if amount > expected_amount or amount < expected_amount * Decimal("0.89"):
            mismatched += 1
    if mismatched == 0:
        log.info("  [OK]   金额一致性抽样 200 单：price × quantity ≈ amount")
    else:
        log.error("  [FAIL] 金额一致性抽样发现 %d 单异常", mismatched)
        failed = True

    if failed:
        raise RuntimeError("数据校验未通过，请检查生成逻辑")

    log.info("全部校验通过")


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.generate_mysql_data",
        description="生成电商业务数据并写入 MySQL",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--reset", action="store_true", help="写入前先清空全部业务表")
    parser.add_argument("--users", type=int, default=None, help="用户数量")
    parser.add_argument("--products", type=int, default=None, help="商品数量")
    parser.add_argument("--orders", type=int, default=None, help="订单数量")
    parser.add_argument("--seed", type=int, default=None, help="随机种子（保证可复现）")
    parser.add_argument("--batch-size", type=int, default=None, help="每批写入行数")
    parser.add_argument(
        "--no-verify", action="store_true", help="跳过写入后的业务关系校验"
    )
    parser.add_argument(
        "--no-snapshot", action="store_true", help="不保存数据集快照"
    )
    return parser


def _apply_overrides(cfg: GenerateConfig, args: argparse.Namespace) -> GenerateConfig:
    """用命令行参数覆盖配置（dataclass 为 frozen，使用 replace）。"""
    from dataclasses import replace

    overrides: dict[str, int] = {}
    if args.users is not None:
        overrides["user_count"] = args.users
    if args.products is not None:
        overrides["product_count"] = args.products
    if args.orders is not None:
        overrides["order_count"] = args.orders
    if args.seed is not None:
        overrides["random_seed"] = args.seed
    if args.batch_size is not None:
        overrides["batch_size"] = args.batch_size
    return replace(cfg, **overrides) if overrides else cfg


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    app_cfg = load_config()
    gen_cfg = _apply_overrides(app_cfg.generate, args)

    log.info("=" * 60)
    log.info("生成 MySQL 业务数据")
    log.info("=" * 60)
    log.info(
        "规模：用户 %d / 商品 %d / 订单 %d",
        gen_cfg.user_count, gen_cfg.product_count, gen_cfg.order_count,
    )

    # 1. 生成数据
    dataset = build_dataset(gen_cfg)

    # 2. 写入 MySQL
    with MySQLClient(app_cfg.mysql) as client:
        # 前置检查：确认表结构已就绪
        for table in ("user", "product", "orders", "payment", "refund"):
            if not client.table_exists(table):
                raise RuntimeError(
                    f"表 {table} 不存在。请确认 MySQL 初始化脚本已执行"
                    "（sql/mysql/01_schema.sql），必要时重建容器与数据卷。"
                )

        if args.reset:
            log.info("--reset：清空现有业务数据 ...")
            client.truncate(TRUNCATE_ORDER)
        else:
            existing = client.count_rows("orders")
            if existing > 0:
                log.warning(
                    "orders 表已有 %d 行数据。继续写入会追加数据；"
                    "如需从零开始，请加 --reset 参数。",
                    existing,
                )

        written = write_dataset(client, dataset, gen_cfg.batch_size)

        if not args.no_verify:
            verify(client)

        log.info("-" * 60)
        log.info("写入结果：")
        for table, count in written.items():
            log.info("  %-10s %d 行", table, count)
        log.info("-" * 60)

    # 3. 保存快照（供 generate_events 使用）
    if not args.no_snapshot:
        save_snapshot(dataset)

    log.info("完成。下一步可执行：python -m src.generate_events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
