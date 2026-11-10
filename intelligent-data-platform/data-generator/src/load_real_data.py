# -*- coding: utf-8 -*-
"""把真实数据集装载进本项目的两条链路入口。

装载对象（由 `scripts/real-data/build_*_dataset.py` 生成）：

    交易域（UCI Online Retail II，2011-11）
        user.csv / product.csv / orders.csv / payment.csv / refund.csv
        -> MySQL 五张表（批量源，Spark 从这里抽）
        -> Kafka order_event / payment_event / refund_event（实时源，Flink 从这里读）

    流量域（天池 UserBehavior，2017-11-30 ~ 2017-12-03）
        tianchi_user.csv / tianchi_product.csv / tianchi_behavior.csv
        -> MySQL user / product（维度，供湖仓 dwd_product_detail 取类目）
        -> Kafka behavior_event

为什么两条链路必须从**同一份 CSV**出发
--------------------------------------
批流对账成立的前提是"实时链路看到的事件"与"批量链路看到的事实"
描述的是同一批业务事件。所以这里从同一份行生成 MySQL 记录与 Kafka 消息，
金额/数量/时间逐字段相同，事件时间原样保留（不做时间平移）。
对账里出现差异就只可能来自**实现错误**或**口径定义**，这正是论文要区分的两类。

用法
----
    # 容器内（推荐，走 compose 的 tools profile）
    docker compose --profile tools run --rm data-generator \
        python -m src.load_real_data --dir data/real

    # 只重置数据库、不发布事件
    ... --step mysql

    # 只发布事件
    ... --step kafka

    # 先看看要写多少行，不落库
    ... --dir data/real --dry-run
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal

from .common import get_logger
from .config import load_config
from .db import MySQLClient
from .kafka_producer import KafkaPublisher

log = get_logger("load_real_data")

#: TRUNCATE 顺序：先子表后父表（外键约束）
TRUNCATE_ORDER = ["refund", "payment", "orders", "product", "user"]

USER_COLUMNS = ["user_id", "username", "gender", "age", "province", "city",
                "user_level", "register_time", "update_time"]
PRODUCT_COLUMNS = ["product_id", "product_name", "category_id", "category_name",
                   "brand", "price", "cost", "status", "create_time", "update_time"]
ORDER_COLUMNS = ["order_id", "user_id", "product_id", "quantity", "amount",
                 "status", "create_time", "pay_time", "update_time"]
PAYMENT_COLUMNS = ["payment_id", "order_id", "user_id", "amount",
                   "payment_method", "payment_status", "payment_time"]
REFUND_COLUMNS = ["refund_id", "order_id", "user_id", "refund_amount",
                  "refund_reason", "refund_status", "refund_time"]

TABLE_COLUMNS = {
    "user": USER_COLUMNS,
    "product": PRODUCT_COLUMNS,
    "orders": ORDER_COLUMNS,
    "payment": PAYMENT_COLUMNS,
    "refund": REFUND_COLUMNS,
}

#: 需要转成 int 的列。
#:
#: !! 不要把 `status` 放进来 !!
#:   `orders.status` 是字符串枚举（PAID / ...），`product.status` 才是 TINYINT。
#:   两列同名不同型，按列名判断类型必然错一边；`product.status` 的 "1"
#:   直接以字符串交给 MySQL，TINYINT 会隐式转换，不需要在 Python 侧转。
_INT_COLUMNS = {
    "user_id", "product_id", "order_id", "payment_id", "refund_id",
    "category_id", "quantity", "age", "gender",
}

#: 金额列**不转 float**，原样以字符串交给 MySQL。
#:
#: 理由：float 会把 "0.39" 变成 0.39000000000000001，再被 DECIMAL(18,2)
#: 舍入回 0.39；单行看不出差别，但一旦参与求和，逐行舍入误差会累积成
#: 分位上的漂移，而批流对账恰恰是按分比 GMV。字符串直传由 MySQL 自己做
#: 十进制解析，结果与源数据逐位相等。
_DECIMAL_COLUMNS = {"amount", "price", "cost", "refund_amount"}


def count_rows(path: str) -> int:
    """只数数据行（不含表头），用于千万行级别时避免把整表读进内存。"""
    if not os.path.isfile(path):
        raise SystemExit("缺少输入文件：%s" % path)
    n = -1
    with open(path, "rb") as f:
        for _ in f:
            n += 1
    return max(n, 0)


def iter_behavior_chunks(path: str, chunk: int = 100_000):
    """按块读取行为明细并逐块产出事件字典。

    千万行级别不能一次性 build 成 list[dict]（10M 个 dict 约 4~6 GB），
    会把 16 GB 的机器压到 OOM。CSV 本身已按 timestamp 升序，
    逐块发送仍满足"每个 topic 水位单调推进"的要求。
    """
    if not os.path.isfile(path):
        raise SystemExit("缺少输入文件：%s" % path)
    buf: list[dict] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        need = ["event_id", "event_type", "user_id", "product_id",
                "category_name", "device", "province", "event_time"]
        missing = [c for c in need if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit("%s 缺少列：%s" % (os.path.basename(path), missing))
        for raw in reader:
            buf.append({
                "event_id": (raw.get("event_id") or "").strip(),
                "event_type": (raw.get("event_type") or "").strip(),
                "user_id": int((raw.get("user_id") or "0").strip()),
                "product_id": int((raw.get("product_id") or "0").strip()),
                "category_name": (raw.get("category_name") or "").strip(),
                "device": (raw.get("device") or "UNKNOWN").strip(),
                "province": (raw.get("province") or "未知").strip(),
                "event_time": (raw.get("event_time") or "").strip(),
            })
            if len(buf) >= chunk:
                yield buf
                buf = []
    if buf:
        yield buf


def _read_csv(path: str, columns: list[str]) -> list[tuple]:
    """读 CSV -> 元组列表，空串转 None，数值列转成对应类型。"""
    if not os.path.isfile(path):
        raise SystemExit("缺少输入文件：%s" % path)
    rows: list[tuple] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit("%s 缺少列：%s" % (os.path.basename(path), missing))
        for raw in reader:
            values = []
            for col in columns:
                v = (raw.get(col) or "").strip()
                if v == "":
                    values.append(None)
                elif col in _INT_COLUMNS:
                    values.append(int(v))
                elif col in _DECIMAL_COLUMNS:
                    # 原样保留十进制字面量，交给 MySQL 解析（见 _DECIMAL_COLUMNS 说明）
                    values.append(v)
                else:
                    values.append(v)
            rows.append(tuple(values))
    return rows


# ---------------------------------------------------------------
# Kafka 事件构造
#
# 字段名与 data-generator/src/kafka_events.py 完全一致，
# Flink 侧的 JSON format 只认这些键。
# ---------------------------------------------------------------
def fmt_amount(value) -> str:
    """把十进制字面量格式化成两位小数的字符串。

    金额在装载链路里全程是 `Decimal`/字符串，**从不经过 float**
    （见 `_DECIMAL_COLUMNS` 的说明）。这里也从字符串直接格式化，
    而不是先 `float()` 再 `"%.2f"`：后者对大额合计会引入分位误差，
    而批流对账正是按分比 GMV 的。
    """
    return format(Decimal(str(value)), ".2f")


def _order_events(orders: list[tuple], product_meta: dict[int, tuple]) -> list[dict]:
    out = []
    for (order_id, user_id, product_id, quantity, amount,
         status, create_time, pay_time, _u) in orders:
        category_name, brand = product_meta.get(product_id, ("", ""))
        out.append({
            "event_id": "1%011d" % order_id,
            "event_type": "ORDER_CREATED",
            "order_id": order_id,
            "user_id": user_id,
            "product_id": product_id,
            "quantity": quantity,
            "amount": fmt_amount(amount),
            "event_time": create_time,
            "category_name": category_name or "",
            "brand": brand or "",
        })
    return out


def _payment_events(payments: list[tuple]) -> list[dict]:
    return [{
        "event_id": "2%011d" % payment_id,
        "event_type": "PAYMENT_SUCCESS" if payment_status == "SUCCESS" else "PAYMENT_FAILED",
        "payment_id": payment_id,
        "order_id": order_id,
        "user_id": user_id,
        "amount": fmt_amount(amount),
        "payment_method": payment_method or "UNKNOWN",
        "event_time": payment_time,
    } for (payment_id, order_id, user_id, amount, payment_method,
           payment_status, payment_time) in payments]


def _refund_events(refunds: list[tuple]) -> list[dict]:
    return [{
        "event_id": "3%011d" % refund_id,
        "event_type": "REFUND_CREATED",
        "refund_id": refund_id,
        "order_id": order_id,
        "user_id": user_id,
        "refund_amount": fmt_amount(refund_amount),
        "event_time": refund_time,
    } for (refund_id, order_id, user_id, refund_amount, _reason,
           _status, refund_time) in refunds]


def _behavior_events(behaviors: list[tuple]) -> list[dict]:
    out = []
    for (event_id, event_type, user_id, product_id, category_name,
         device, province, event_time) in behaviors:
        out.append({
            "event_id": event_id,
            "event_type": event_type,
            "user_id": user_id,
            "product_id": product_id,
            "category_name": category_name or "",
            "device": device or "UNKNOWN",
            "province": province or "未知",
            "event_time": event_time,
        })
    return out


def load_mysql(client: MySQLClient, data: dict[str, list[tuple]]) -> None:
    log.info("清空 MySQL（顺序：%s）", " -> ".join(TRUNCATE_ORDER))
    client.truncate(TRUNCATE_ORDER)
    for table in ("user", "product", "orders", "payment", "refund"):
        rows = data[table]
        cols = TABLE_COLUMNS[table]
        log.info("写入 %-8s %s 行", table, format(len(rows), ","))
        client.insert_many(table, cols, rows)
        actual = client.count_rows(table)
        if actual != len(rows):
            raise SystemExit("表 %s 行数不符：期望 %d，实际 %d" % (table, len(rows), actual))


def publish_kafka(publisher: KafkaPublisher, events: list[tuple[str, str, dict]]) -> Counter:
    """按事件时间顺序发布，保证每个 topic 的水位单调推进。

    Flink 用 event_time - 5s 作 watermark；如果先发完某个 topic 再做别的，
    跨 topic 的窗口会因为在途数据迟到而被丢弃。
    """
    events.sort(key=lambda x: (x[0], x[1]))
    sent = Counter()
    for _ts, topic, payload in events:
        key = str(payload.get("order_id") or payload.get("payment_id")
                  or payload.get("refund_id") or payload.get("user_id"))
        publisher.publish(topic, key, payload)
        sent[topic] += 1
    publisher.flush(timeout=600.0)
    publisher.assert_no_failures()
    return sent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="真实数据集 -> MySQL / Kafka 装载")
    ap.add_argument("--dir", default="data/real", help="装载 CSV 所在目录")
    ap.add_argument("--step", default="all", choices=["all", "mysql", "kafka"])
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    ap.add_argument("--chunk", type=int, default=100_000,
                    help="流量域每块读取行数（默认 10 万，控制内存）")
    args = ap.parse_args(argv)

    root = args.dir
    data = {
        "user": _read_csv(os.path.join(root, "user.csv"), USER_COLUMNS)
        + _read_csv(os.path.join(root, "tianchi_user.csv"), USER_COLUMNS),
        "product": _read_csv(os.path.join(root, "product.csv"), PRODUCT_COLUMNS)
        + _read_csv(os.path.join(root, "tianchi_product.csv"), PRODUCT_COLUMNS),
        "orders": _read_csv(os.path.join(root, "orders.csv"), ORDER_COLUMNS),
        "payment": _read_csv(os.path.join(root, "payment.csv"), PAYMENT_COLUMNS),
        "refund": _read_csv(os.path.join(root, "refund.csv"), REFUND_COLUMNS),
    }
    # 行为明细按块流式处理：千万行级别不能整表进内存
    behavior_path = os.path.join(root, "tianchi_behavior.csv")
    behavior_count = count_rows(behavior_path)

    # ---- 主键唯一性守卫（在写库之前就得挡住） ----
    for table in ("user", "product"):
        ids = [r[0] for r in data[table]]
        if len(ids) != len(set(ids)):
            raise SystemExit("表 %s 主键重复：%d 行 / %d 个不同 id"
                             % (table, len(ids), len(set(ids))))

    total_gmv = sum((Decimal(r[4]) for r in data["orders"] if r[4]), Decimal(0))
    total_refund = sum((Decimal(r[3]) for r in data["refund"] if r[3]), Decimal(0))
    summary = {
        "user": len(data["user"]),
        "product": len(data["product"]),
        "orders": len(data["orders"]),
        "payment": len(data["payment"]),
        "refund": len(data["refund"]),
        "behavior": behavior_count,
    }
    print("=" * 62)
    for k, v in summary.items():
        print("  %-10s %s" % (k, format(v, ",")))
    print("  %-10s %s" % ("GMV", format(total_gmv, ",.2f")))
    print("  %-10s %s" % ("退款额", format(total_refund, ",.2f")))
    print("=" * 62)

    if args.dry_run:
        log.info("--dry-run：不写库、不发消息")
        return 0

    cfg = load_config()

    if args.step in ("all", "mysql"):
        with MySQLClient(cfg.mysql) as client:
            load_mysql(client, data)
            log.info("MySQL 装载完成（%s / %s）", cfg.mysql.host, cfg.mysql.database)

    if args.step in ("all", "kafka"):
        product_meta = {r[0]: (r[3], r[4]) for r in data["product"]}
        # 交易域三张表体量在百万级，整体排序后再发（保证 order/payment/refund
        # 三个 topic 的时间水位同步推进）；流量域单独成流，跨域不需要对齐。
        events: list[tuple[str, str, dict]] = []
        for payload in _order_events(data["orders"], product_meta):
            events.append((payload["event_time"], "order_event", payload))
        for payload in _payment_events(data["payment"]):
            events.append((payload["event_time"], "payment_event", payload))
        for payload in _refund_events(data["refund"]):
            events.append((payload["event_time"], "refund_event", payload))
        log.info("交易域待发布事件 %s 条（按事件时间排序后发送）", format(len(events), ","))
        publisher = KafkaPublisher(cfg.kafka)
        sent = publish_kafka(publisher, events)
        del events

        # 流量域：CSV 已按 timestamp 升序，分块发送
        log.info("流量域待发布事件 %s 条（分块 %s 行发送）", format(behavior_count, ","),
                 format(args.chunk, ","))
        for chunk in iter_behavior_chunks(behavior_path, args.chunk):
            for payload in chunk:
                publisher.publish("behavior_event", str(payload["user_id"]), payload)
                sent["behavior_event"] += 1
        publisher.flush(timeout=1800.0)
        publisher.assert_no_failures()
        for topic in sorted(sent):
            log.info("  %-16s %s", topic, format(sent[topic], ","))

    log.info("装载完成：%s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
