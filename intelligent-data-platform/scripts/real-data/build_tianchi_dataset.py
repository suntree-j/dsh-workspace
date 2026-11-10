# -*- coding: utf-8 -*-
"""把天池 UserBehavior 抽样结果转成本项目的流量域输入（维度 + 行为事件）。

数据来源
--------
Alibaba Group. Taobao user behavior data set[DS/OL]. Hangzhou: Tianchi Platform,
2017[2026-11-06]. https://tianchi.aliyun.com/dataset/649.  (CC BY-SA-NC 4.0)

输入是天池全量 100,150,807 行按**用户**抽样后的结果（见 tianchi_sample.py）：
5,000 个用户、2017-11-30 ~ 2017-12-03 三个自然日、175,371 条行为。
保留原始事件时间戳，不做任何平移。

口径映射（源数据只有 4 级行为，本项目漏斗相应为 3 级）
------------------------------------------------------
pv     -> VIEW
cart   -> CART
fav    -> FAVORITE
buy    -> BUY
（源数据**没有 CLICK**，click_cnt 在该域恒为 0，口径说明中必须写明）

ID 空间
-------
天池与 UCI 在用户、商品、类目上**完全不重叠**，但 MySQL 的 user / product 是
单表主键，因此天池侧统一加偏移量 `10_000_000`，使两域主键天然不相撞：
    user_id     = 10_000_000 + user_id
    product_id  = 10_000_000 + item_id
    category_id = 10_000_000 + category_id   （UCI 派生类目占 1~11，不冲突）

用法
----
    python scripts/real-data/build_tianchi_dataset.py [--src CSV] [--out DIR]
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_SRC = os.path.join(ROOT, ".tmp", "realdata", "tianchi", "traffic_events.csv")
DEFAULT_OUT = os.path.join(ROOT, ".tmp", "realdata", "out")

OFFSET = 10_000_000
TZ = "Asia/Shanghai"

#: 源数据行为 -> 本项目 event_type
BEHAVIOR_MAP = {"pv": "VIEW", "cart": "CART", "fav": "FAVORITE", "buy": "BUY"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="天池抽样 -> 流量域装载 CSV")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    raw = pd.read_csv(args.src)
    print("读入 %s 行：%s" % (format(len(raw), ","), args.src))

    unknown = set(raw["behavior_type"].unique()) - set(BEHAVIOR_MAP)
    if unknown:
        raise SystemExit("出现未映射的行为类型：%s" % sorted(unknown))

    df = raw.copy()
    df["event_type"] = df["behavior_type"].map(BEHAVIOR_MAP)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(TZ)
    df["event_time"] = df["ts"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df["user_id"] = df["user_id"].astype("int64") + OFFSET
    df["product_id"] = df["item_id"].astype("int64") + OFFSET
    df["category_id"] = df["category_id"].astype("int64") + OFFSET
    df = df.sort_values(["timestamp", "user_id", "product_id", "event_type"],
                        kind="mergesort").reset_index(drop=True)

    # ---- 维度：用户 -----------------------------------------------------
    first_seen = df.groupby("user_id")["event_time"].min()
    users = pd.DataFrame({
        "user_id": first_seen.index.astype("int64"),
        "username": ["taobao_user_%d" % (u - OFFSET) for u in first_seen.index],
        "gender": "",
        "age": "",
        "province": "未知",
        "city": "未知",
        "user_level": "",
        "register_time": first_seen.values,
        "update_time": first_seen.values,
    })

    # ---- 维度：商品 -----------------------------------------------------
    #  源数据只有 item_id 与 category_id，**没有商品名**。
    #  product_name 用 "item_<原始 id>" 作为可读标识，不编造业务名称。
    #
    #  注意：同一个 item 在源数据里可能挂过多个 category_id（类目漂移）。
    #  若按 (product_id, category_id) 分组，一个商品会写出多行，product 主键
    #  就重复了（1,598,973 行 / 1,598,617 个 id）。这里按商品出现次数取
    #  「主导类目」——只留一行，且口径可解释：该商品出现最多的类目。
    pair = df.groupby(["product_id", "category_id"]).size().reset_index(name="n")
    items = (pair.sort_values(["product_id", "n"], ascending=[True, False])
                 .drop_duplicates("product_id")
                 .drop(columns=["n"]))
    assert items["product_id"].is_unique, "商品维度仍有重复主键"
    products = pd.DataFrame({
        "product_id": items["product_id"].astype("int64"),
        "product_name": ["tb_item_%d" % (p - OFFSET) for p in items["product_id"]],
        "category_id": items["category_id"].astype("int64"),
        "category_name": ["天池类目%d" % (c - OFFSET) for c in items["category_id"]],
        "brand": "",
        "price": "",
        "cost": "",
        "status": 1,
        "create_time": "",
        "update_time": "",
    })

    # ---- 事实：行为事件 -------------------------------------------------
    events = pd.DataFrame({
        "event_id": ["4%010d" % i for i in range(1, len(df) + 1)],
        "event_type": df["event_type"],
        "user_id": df["user_id"].astype("int64"),
        "product_id": df["product_id"].astype("int64"),
        "category_name": ["天池类目%d" % (c - OFFSET) for c in df["category_id"]],
        "device": "UNKNOWN",
        "province": "未知",
        "event_time": df["event_time"],
    })

    # ---- 自检 -----------------------------------------------------------
    assert events["event_id"].is_unique, "event_id 不唯一"
    assert events["user_id"].isin(users["user_id"]).all(), "事件 user_id 悬空"
    assert events["product_id"].isin(products["product_id"]).all(), "事件 product_id 悬空"
    by_type = events["event_type"].value_counts()
    assert by_type.get("VIEW", 0) > by_type.get("CART", 0) > by_type.get("BUY", 0), "三级漏斗不单调"

    os.makedirs(args.out, exist_ok=True)
    users.to_csv(os.path.join(args.out, "tianchi_user.csv"), index=False, encoding="utf-8")
    products.to_csv(os.path.join(args.out, "tianchi_product.csv"), index=False, encoding="utf-8")
    events.to_csv(os.path.join(args.out, "tianchi_behavior.csv"), index=False, encoding="utf-8")

    days = df["ts"].dt.strftime("%Y-%m-%d").value_counts().sort_index()
    lines = ["# 天池 UserBehavior -> 流量域装载数据集\n"]
    lines.append("- 源：`%s`（抽样后 %s 行）" % (args.src, format(len(raw), ",")))
    lines.append("- 时间窗：2017-11-30 ~ 2017-12-03（+08，保留原始时间戳，未平移）")
    lines.append("- ID 偏移：`+%s`（与交易域主键天然不重叠）" % format(OFFSET, ","))
    lines.append("")
    lines.append("## 装载规模\n")
    lines.append("| 表/文件 | 行数 |")
    lines.append("| --- | ---: |")
    lines.append("| user（天池） | %s |" % format(len(users), ","))
    lines.append("| product（天池） | %s |" % format(len(products), ","))
    lines.append("| behavior_event（天池） | %s |" % format(len(events), ","))
    lines.append("")
    lines.append("## 行为映射（源数据无 CLICK）\n")
    lines.append("| 源 behavior_type | 本项目 event_type | 条数 |")
    lines.append("| --- | --- | ---: |")
    for src, dst in BEHAVIOR_MAP.items():
        n = int((raw["behavior_type"] == src).sum())
        lines.append("| %s | %s | %s |" % (src, dst, format(n, ",")))
    lines.append("")
    lines.append("## 逐日事件数\n")
    lines.append("| 日期 | 事件数 |")
    lines.append("| --- | ---: |")
    for d, n in days.items():
        lines.append("| %s | %s |" % (d, format(int(n), ",")))
    lines.append("")
    lines.append("## 三级漏斗（去重用户 / 事件数）\n")
    lines.append("| 层级 | 事件数 | 去重用户 |")
    lines.append("| --- | ---: | ---: |")
    for name in ("VIEW", "CART", "FAVORITE", "BUY"):
        g = events[events["event_type"] == name]
        lines.append("| %s | %s | %s |" % (name, format(len(g), ","), format(g["user_id"].nunique(), ",")))
    lines.append("")
    lines.append("- 唯一用户：%s；唯一商品：%s；唯一类目：%s" % (
        format(events["user_id"].nunique(), ","),
        format(events["product_id"].nunique(), ","),
        format(events["category_name"].nunique(), ",")))
    lines.append("- 有事件的分钟数：%s / 4320（%.1f%%）" % (
        format(df["timestamp"].floordiv(60).nunique(), ","),
        100.0 * df["timestamp"].floordiv(60).nunique() / 4320))

    md = os.path.join(args.out, "TIANCHI_DATASET.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\n写出 -> %s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
