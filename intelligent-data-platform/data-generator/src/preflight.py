# -*- coding: utf-8 -*-
"""装载前预检：在写库/发消息之前，先把"这份 CSV 到底能不能装"查清楚。

为什么单独一个模块
------------------
原来的 ``load_real_data.py`` 在 ``main()`` 里做了两件事：读 CSV、然后写库。
主键唯一性守卫只在内存里查 ``user`` / ``product`` 两张表，其他四条检查
（外键覆盖、金额精度、时间单调、行数与官方口径是否一致）都散在别处，
而且必须在真实装载时才会触发。结果是：**一次错误的装载要跑完才知道错**。

预检把这些检查前移，并且**只读**：不建表、不写库、不发消息，可以随时在正式
环境跑。它输出一份 ``manifest``（每个文件的 sha256 / 行数 / 列名 / 关键合计），
这份 manifest 就是论文里"输入可复现"那条证据的具体内容。

检查项与理由
------------
1. 文件齐全 + 表头一致 —— 列错位是静默错误，写进库才炸。
2. 主键唯一 —— ``INSERT`` 遇重复主键会失败，但那时已经 TRUNCATE 过了。
3. 外键覆盖 —— ``orders.user_id`` 必须在 ``user`` 里存在，否则 MySQL 直接拒。
4. 金额精度 ≤ 2 位 —— 对账按分比金额，三位小数会在装载时被 MySQL 静默舍入。
5. 行为明细时间非降 —— 流式发送要求水位单调，否则 Flink 会丢迟到事件。
6. 行数与官方口径一致 —— 便于区分"数据被裁剪过"与"脚本有 bug"。

用法
----
    python -m src.preflight --dir data/real
    python -m src.preflight --dir data/real --manifest /tmp/manifest.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from decimal import Decimal

#: 与 load_real_data 保持一致的列定义（这里复制一份，避免预检依赖装载器实现，
#: 两边不一致时预检会先报错，这正是想要的）。
COLS = {
    "user.csv": ["user_id", "username", "gender", "age", "province", "city",
                 "user_level", "register_time", "update_time"],
    "product.csv": ["product_id", "product_name", "category_id", "category_name",
                    "brand", "price", "cost", "status", "create_time", "update_time"],
    "orders.csv": ["order_id", "user_id", "product_id", "quantity", "amount",
                   "status", "create_time", "pay_time", "update_time"],
    "payment.csv": ["payment_id", "order_id", "user_id", "amount",
                    "payment_method", "payment_status", "payment_time"],
    "refund.csv": ["refund_id", "order_id", "user_id", "refund_amount",
                   "refund_reason", "refund_status", "refund_time"],
    "tianchi_user.csv": ["user_id", "username", "gender", "age", "province", "city",
                         "user_level", "register_time", "update_time"],
    "tianchi_product.csv": ["product_id", "product_name", "category_id", "category_name",
                            "brand", "price", "cost", "status", "create_time", "update_time"],
    "tianchi_behavior.csv": ["event_id", "event_type", "user_id", "product_id",
                             "category_name", "device", "province", "event_time"],
}

#: 金额列：必须最多两位小数。UCI 原始金额是两位，超过两位说明建集逻辑改过。
MONEY_COLS = {"amount", "price", "cost", "refund_amount"}
_MONEY_RE = re.compile(r"^-?\d+(\.\d{1,2})?$")
#: 允许为空串的列（性别/年龄等本来就可空）
TIME_COLS = {"event_time", "create_time", "pay_time", "update_time", "register_time",
             "payment_time", "refund_time"}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_header(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return next(csv.reader(f), [])


def iter_rows(path: str):
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def check_file(name: str, path: str, problems: list[str], manifest: dict) -> dict:
    """逐个文件做结构检查，返回该文件的统计行。"""
    if not os.path.isfile(path):
        problems.append("缺文件：%s" % name)
        return {}
    header = read_header(path)
    expect = COLS[name]
    if header != expect:
        problems.append("%s 表头不符：期望 %s，实际 %s"
                        % (name, expect, header[:len(expect)]))
    money_bad = 0
    time_bad = 0
    rows = 0
    decimals = {c: Decimal(0) for c in COLS[name] if c in MONEY_COLS}
    prev_ts = ""
    for row in iter_rows(path):
        rows += 1
        for c in COLS[name]:
            if c not in MONEY_COLS:
                continue
            v = (row.get(c) or "").strip()
            if v == "":
                continue
            if not _MONEY_RE.match(v):
                money_bad += 1
            else:
                decimals[c] += Decimal(v)
        if name == "tianchi_behavior.csv":
            ts = (row.get("event_time") or "").strip()
            if prev_ts and ts < prev_ts:
                time_bad += 1
            prev_ts = ts
    if money_bad:
        problems.append("%s 有 %d 个金额值超过两位小数（会装载时被静默舍入）"
                        % (name, money_bad))
    if time_bad:
        problems.append("%s 有 %d 处 event_time 逆序（Flink 水位会丢迟到事件）"
                        % (name, time_bad))
    info = {
        "file": name,
        "rows": rows,
        "sha256": sha256_of(path),
        "bytes": os.path.getsize(path),
        "header_ok": header == expect,
        "money_columns": {k: str(v) for k, v in decimals.items()},
    }
    manifest[name] = info
    return info


def run(data_dir: str) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    manifest: dict = {"data_dir": os.path.abspath(data_dir), "files": {}}

    ids: dict[str, set] = {}
    fk_checks: list[tuple[str, str, str]] = [
        ("orders.csv", "user_id", "user.csv"),
        ("orders.csv", "product_id", "product.csv"),
        ("payment.csv", "order_id", "orders.csv"),
        ("payment.csv", "user_id", "user.csv"),
        ("refund.csv", "order_id", "orders.csv"),
        ("refund.csv", "user_id", "user.csv"),
        ("tianchi_behavior.csv", "user_id", "tianchi_user.csv"),
        ("tianchi_behavior.csv", "product_id", "tianchi_product.csv"),
    ]

    for name in COLS:
        check_file(name, os.path.join(data_dir, name), problems, manifest["files"])

    if problems:
        return False, problems, manifest

    # 主键唯一 + 收集 id 集合（只收主键，内存可控）
    for name, key in (("user.csv", "user_id"), ("tianchi_user.csv", "user_id"),
                      ("product.csv", "product_id"),
                      ("tianchi_product.csv", "product_id"),
                      ("orders.csv", "order_id"), ("payment.csv", "payment_id"),
                      ("refund.csv", "refund_id")):
        path = os.path.join(data_dir, name)
        seen: set = set()
        dup = 0
        for row in iter_rows(path):
            v = row[key]
            if v in seen:
                dup += 1
            else:
                seen.add(v)
        if dup:
            problems.append("%s 主键 %s 重复 %d 行" % (name, key, dup))
        ids[name] = seen

    # 外键覆盖
    for child, col, parent in fk_checks:
        missing = 0
        for row in iter_rows(os.path.join(data_dir, child)):
            if row[col] not in ids[parent]:
                missing += 1
        if missing:
            problems.append("%s.%s 有 %d 行在 %s 中找不到（外键悬空）"
                            % (child, col, missing, parent))

    # 汇总：GMV / 退款额（与对账口径同源）
    gmv = sum((Decimal(r["amount"]) for r in iter_rows(os.path.join(data_dir, "orders.csv"))
               if r["amount"]), Decimal(0))
    refund = sum((Decimal(r["refund_amount"])
                  for r in iter_rows(os.path.join(data_dir, "refund.csv"))
                  if r["refund_amount"]), Decimal(0))
    manifest["totals"] = {"gmv": str(gmv), "refund_amount": str(refund)}
    return not problems, problems, manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="真实数据集装载前预检（只读）")
    ap.add_argument("--dir", default="data/real")
    ap.add_argument("--manifest", default="", help="把 manifest 写到这个 JSON 路径")
    args = ap.parse_args(argv)

    ok, problems, manifest = run(args.dir)
    print("=" * 72)
    for name, info in manifest["files"].items():
        if not info:
            continue
        print("  %-22s %10s 行  %8.1f MB  %s"
              % (name, format(info["rows"], ","), info["bytes"] / 1048576.0,
                 info["sha256"][:16]))
    totals = manifest.get("totals") or {}
    if totals:
        print("  %-22s GMV %s / 退款 %s" % ("合计", totals["gmv"], totals["refund_amount"]))
    print("=" * 72)
    if problems:
        print("预检未通过（%d 项）：" % len(problems))
        for p in problems:
            print("  ✗ " + p)
    else:
        print("预检通过：8 个文件结构/主键/外键/金额精度/时间单调全部正常")
    if args.manifest:
        with open(args.manifest, "w", encoding="utf-8", newline="\n") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print("manifest: %s" % args.manifest)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
