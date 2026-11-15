# -*- coding: utf-8 -*-
"""装载中断演练：在隔离 schema 里模拟"装到一半被杀"，再重跑，验证不残留脏数据。

为什么需要在隔离 schema 里做
---------------------------
正式库的装载路径是 ``TRUNCATE -> 逐表 INSERT``。如果进程在中间被杀，正式库会
停在一个"部分表已清空、部分表已写入"的状态，而这段时间批处理/对账照跑的话，
结论就是脏的。这条风险不该拿正式库来验证，所以本模块：

1. 建一个隔离 schema（默认 ``ecommerce_drill``），用 ``CREATE TABLE ... LIKE``
   克隆正式库五张表的结构（只克隆结构，不碰数据）；
2. 从真实的 8 个 CSV 里切一个**外键自洽的小样本**（取前 N 张订单，并只保留被
   引用到的 user / product，以及属于这些订单的 payment / refund）；
3. 装到第 K 批时**主动抛出异常**，模拟进程被杀，记录此刻各表行数；
4. 重新完整装载一次，断言行数与金额合计都等于"一次装完"的期望值；
5. 演练结束 ``DROP DATABASE``（除非 ``--keep``）。

它证明的是一条具体承诺：**重跑必须收敛到同一状态**。这对论文里"输入可复现"
那一节是直接证据，也是运维文档里"装载失败怎么办"的答案。

用法
----
    docker compose --profile tools run --rm data-generator \
        python -m src.drill_load --dir data/real --limit 5000 --fail-after-batches 2

    # 保留演练库以便人工查看
    ... --keep
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import replace
from decimal import Decimal

from .common import get_logger
from .config import load_config
from .db import MySQLClient
from .load_real_data import (ORDER_COLUMNS, PAYMENT_COLUMNS, PRODUCT_COLUMNS,
                             REFUND_COLUMNS, TABLE_COLUMNS, USER_COLUMNS,
                             _read_csv)

log = get_logger("drill_load")

TABLES = ["user", "product", "orders", "payment", "refund"]


class SimulatedCrash(RuntimeError):
    """模拟进程在装载途中被杀。"""


def _read(name: str, columns: list[str], root: str) -> list[tuple]:
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        raise SystemExit("缺少输入文件：%s" % path)
    return _read_csv(path, columns)


def build_sample(root: str, limit: int) -> dict[str, list[tuple]]:
    """切一个外键自洽的小样本：订单取前 N 条，其余表按引用过滤。"""
    users = _read("user.csv", USER_COLUMNS, root)
    products = _read("product.csv", PRODUCT_COLUMNS, root)
    orders = _read("orders.csv", ORDER_COLUMNS, root)[:limit]
    payments = _read("payment.csv", PAYMENT_COLUMNS, root)
    refunds = _read("refund.csv", REFUND_COLUMNS, root)

    order_ids = {r[0] for r in orders}
    user_ids = {r[1] for r in orders}          # orders.user_id
    product_ids = {r[2] for r in orders}       # orders.product_id

    # 支付/退款只保留属于这些订单的部分；user/product 只保留被引用到的部分。
    # 注意 payment.user_id 可能属于未被引用的用户，这里一并纳入，保证外键成立。
    payments = [p for p in payments if p[1] in order_ids]
    refunds = [r for r in refunds if r[1] in order_ids]
    user_ids |= {p[2] for p in payments} | {r[2] for r in refunds}
    users = [u for u in users if u[0] in user_ids]
    products = [p for p in products if p[0] in product_ids]

    return {"user": users, "product": products, "orders": orders,
            "payment": payments, "refund": refunds}


def clone_schema(client: MySQLClient, source_db: str, drill_db: str) -> None:
    cur = client.connection.cursor()
    # 业务账号通常没有 CREATE DATABASE 权限，演练库由管理员预先建好并授权；
    # 有权限时顺手建，没有就跳过，不把它当成失败条件。
    try:
        cur.execute("CREATE DATABASE IF NOT EXISTS `%s`" % drill_db)
    except Exception as exc:                                  # noqa: BLE001
        log.info("建库跳过（%s）——假定演练库已由管理员创建并授权", exc.__class__.__name__)
    for table in TABLES:
        cur.execute("DROP TABLE IF EXISTS `%s`.`%s`" % (drill_db, table))
        cur.execute("CREATE TABLE `%s`.`%s` LIKE `%s`.`%s`"
                    % (drill_db, table, source_db, table))
    client.connection.commit()
    log.info("已克隆表结构：%s <- %s（%d 张）", drill_db, source_db, len(TABLES))


def drop_schema(client: MySQLClient, drill_db: str) -> None:
    cur = client.connection.cursor()
    try:
        cur.execute("DROP DATABASE IF EXISTS `%s`" % drill_db)
        client.connection.commit()
        log.info("已删除演练库 %s", drill_db)
    except Exception as exc:                                  # noqa: BLE001
        log.info("删除演练库跳过（%s）——请管理员清理 %s", exc.__class__.__name__, drill_db)


def insert_batches(client: MySQLClient, db: str, table: str, rows: list[tuple],
                   batch: int = 1000, fail_after: int = 0) -> int:
    """逐批写入并提交；``fail_after`` 批之后主动抛异常模拟被杀。"""
    cols = TABLE_COLUMNS[table]
    placeholders = ", ".join(["%s"] * len(cols))
    sql = "INSERT INTO `%s`.`%s` (%s) VALUES (%s)" % (
        db, table, ", ".join("`%s`" % c for c in cols), placeholders)
    cur = client.connection.cursor()
    batches = 0
    for i in range(0, len(rows), batch):
        cur.executemany(sql, rows[i:i + batch])
        client.connection.commit()          # 每批一个事务，边界与正式装载一致
        batches += 1
        if fail_after and batches >= fail_after:
            raise SimulatedCrash(
                "模拟在第 %d 批提交后被杀（表 %s 已写 %d 行）"
                % (batches, table, min(batches * batch, len(rows))))
    return batches


def counts(client: MySQLClient, db: str) -> dict[str, int]:
    cur = client.connection.cursor()
    out = {}
    for table in TABLES:
        cur.execute("SELECT COUNT(*) FROM `%s`.`%s`" % (db, table))
        out[table] = int(cur.fetchone()[0])
    return out


def money(client: MySQLClient, db: str) -> str:
    cur = client.connection.cursor()
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM `%s`.`orders`" % db)
    row = cur.fetchone()
    return str(Decimal(str(row[0])).quantize(Decimal("0.01")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="装载中断演练（隔离 schema，不碰正式库）")
    ap.add_argument("--dir", default="data/real")
    ap.add_argument("--schema", default="ecommerce_drill")
    ap.add_argument("--limit", type=int, default=5000, help="取前 N 张订单做样本")
    ap.add_argument("--fail-after-batches", type=int, default=2)
    ap.add_argument("--fail-in", default="orders",
                    help="在第几张表上模拟崩溃（默认 orders，空串表示从第一张表就开始）")
    ap.add_argument("--batch", type=int, default=1000)
    ap.add_argument("--keep", action="store_true", help="演练后保留演练库")
    args = ap.parse_args(argv)

    cfg = load_config()
    source_db = cfg.mysql.database
    if args.schema == source_db:
        raise SystemExit("演练库不能是正式库 %s" % source_db)

    sample = build_sample(args.dir, args.limit)
    expected = {k: len(v) for k, v in sample.items()}
    expected_gmv = sum((Decimal(r[4]) for r in sample["orders"] if r[4]), Decimal(0))
    log.info("样本规模：%s；订单金额合计 %s",
             " / ".join("%s=%d" % (k, v) for k, v in expected.items()),
             format(expected_gmv, ",.2f"))

    drill_cfg = replace(cfg.mysql, database=args.schema)
    with MySQLClient(drill_cfg) as client:
        clone_schema(client, source_db, args.schema)

        # ---- 第一次：装到第 K 批被打断 ----
        log.info("第一次装载（将在第 %d 批后模拟被杀）", args.fail_after_batches)
        crashed_at = {}
        try:
            for table in TABLES:
                hit = args.fail_after_batches if (not args.fail_in or table == args.fail_in) else 0
                insert_batches(client, args.schema, table, sample[table],
                               args.batch, hit)
        except SimulatedCrash as exc:
            crashed_at = counts(client, args.schema)
            log.warning("已模拟崩溃：%s", exc)
            log.warning("此刻各表行数：%s", crashed_at)

        # ---- 第二次：完整重跑（先 TRUNCATE，与正式装载一致） ----
        log.info("第二次装载：TRUNCATE 后完整重跑")
        cur = client.connection.cursor()
        for table in TABLES:
            cur.execute("TRUNCATE TABLE `%s`.`%s`" % (args.schema, table))
        client.connection.commit()
        for table in TABLES:
            insert_batches(client, args.schema, table, sample[table], args.batch)

        final = counts(client, args.schema)
        final_gmv = money(client, args.schema)

        print("=" * 72)
        print("  阶段             %s" % "  ".join("%-9s" % t for t in TABLES))
        print("  中断时（部分）   %s"
              % "  ".join("%-9s" % format(crashed_at.get(t, 0), ",") for t in TABLES))
        print("  重跑后（完整）   %s"
              % "  ".join("%-9s" % format(final[t], ",") for t in TABLES))
        print("  期望             %s"
              % "  ".join("%-9s" % format(expected[t], ",") for t in TABLES))
        print("  重跑后 GMV       %s（期望 %s）"
              % (final_gmv, format(expected_gmv, ",.2f")))
        print("=" * 72)

        ok = (final == expected) and (Decimal(final_gmv) == expected_gmv.quantize(Decimal("0.01")))
        partial = any(crashed_at.get(t, 0) != expected[t] for t in TABLES)
        print("  中断确实留下了部分数据：%s" % ("是" if partial else "否（样本太小，可减小 --limit）"))
        print("  崩溃位置：%s 第 %d 批后" % (args.fail_in or "(第一张表)", args.fail_after_batches))
        print("  重跑是否收敛到期望值：%s" % ("是" if ok else "否"))

        if args.keep:
            log.info("保留演练库 %s（--keep）", args.schema)
        else:
            drop_schema(client, args.schema)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
