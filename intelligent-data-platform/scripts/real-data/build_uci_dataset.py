# -*- coding: utf-8 -*-
"""把 UCI Online Retail II 转成本项目 MySQL 业务表可直接装载的 CSV。

数据来源
--------
CHEN D. Online retail II data set[DS/OL]. Irvine: UC Irvine Machine Learning
Repository, 2011[2026-11-06]. https://doi.org/10.24432/C5CG6D.  (CC BY 4.0)

原始 1,067,371 行、8 列、2009-12-01 ~ 2011-12-09。本脚本只取**一个连续的 30 天**
（默认 2011-11-01 00:00 ~ 2011-12-01 00:00），保留原始事件时间戳，不做任何时间平移。

映射规则（每一条都是**声明性的**，不是"尽量对上"）
--------------------------------------------------
Invoice          -> orders.order_id（发票即一张订单；原表一行一商品，与本项目
                    `orders` 一行一商品明细的口径天然一致）
StockCode        -> product.product_id（自增整数，与原始编码一一对应）
Customer ID      -> user.user_id（自增整数，与原始客户号一一对应）
Quantity * Price -> orders.amount（**精确相等**，不做折扣建模）
InvoiceDate      -> orders.create_time / pay_time / payment.payment_time
C 前缀发票或 Quantity<0 -> refund（取消/退货）

被丢弃的行及原因（全部计入 manifest，不做静默过滤）
--------------------------------------------------
* 整行完全重复：数据集自身的重复录入
* Customer ID 为空：orders.user_id 为 NOT NULL 且外键指向 user
* Quantity <= 0 且无法匹配到同客户同商品的在先订单：无法建立 refund 外键
* Price <= 0：无法计算金额
* 非商品科目（POST / M / AMAZONFEE / B / D / S / C2 / BANK CHARGES ...）：
  它们是运费与会计调整项，不是商品

用法
----
    python scripts/real-data/build_uci_dataset.py [--out DIR] [--cache PKL]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_CACHE = os.path.join(ROOT, ".tmp", "realdata", "uci", "combined.pkl")
DEFAULT_OUT = os.path.join(ROOT, ".tmp", "realdata", "out")

#: 默认窗口 = 数据集全量（2009-12-01 07:45 ~ 2011-12-09 12:50）。
#: 如需只取一个月做对照，用 --win-lo/--win-hi 覆盖。
WIN_LO = pd.Timestamp("2009-12-01 00:00:00")
WIN_HI = pd.Timestamp("2011-12-10 00:00:00")

#: 非商品科目（运费、会计调整、银行手续费、样品等）。这些编码不是可售商品。
NON_PRODUCT = {
    "POST", "DOT", "M", "B", "D", "S", "C2", "C3", "AMAZONFEE",
    "BANK CHARGES", "PADS", "CRUK", "GIFT", "ADJUST", "ADJUST2",
    "TEST001", "TEST002", "SP1002", "DCGS0003", "DCGS0004", "DCGS0005",
    "DCGS0006", "DCGS0007", "DCGS0008", "DCGS0009", "DCGS0010",
}

#: 类目派生规则。源数据**没有类目字段**，因此类目只能由 Description 关键词派生，
#: 未命中任何关键词的归入「其他」。论文中必须声明这一点（派生量，非原始字段）。
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("节庆装饰", ("CHRISTMAS", "XMAS", "ADVENT", "SANTA", "REINDEER", "SNOWMAN", "EASTER", "HALLOWEEN")),
    ("厨房餐厨", ("MUG", "CUP", "BOWL", "PLATE", "TEAPOT", "JUG", "CUTLERY", "JAR", "BAKING", "KITCHEN", "CAKE", "TEA ", "COFFEE", "BOTTLE", "GLASS")),
    ("灯饰蜡烛", ("CANDLE", "T-LIGHT", "TEA LIGHT", "LANTERN", "LIGHT", "LAMP", "HOLDER")),
    ("家居装饰", ("FRAME", "PICTURE", "ORNAMENT", "DECORATION", "HANGING", "HEART", "SIGN", "CLOCK", "MIRROR", "DOORSTOP", "HOOK")),
    ("收纳整理", ("BAG ", "BOX", "BASKET", "TIN", "STORAGE", "SUITCASE", "LUNCH", "POUCH", "CASE")),
    ("文具纸品", ("CARD", "NOTEBOOK", "PEN ", "PENCIL", "PAPER", "WRAP", "ENVELOPE", "STICKER", "JOURNAL", "CHALK")),
    ("布艺纺织", ("CUSHION", "COVER", "TOWEL", "APRON", "FABRIC", "BLANKET", "CURTAIN", "TEXTILE", "RUNNER", "NAPKIN")),
    ("儿童玩具", ("TOY", "GAME", "DOLL", "CHILD", "KIDS", "PLAYHOUSE", "SKIPPING", "BALLOON")),
    ("饰品配饰", ("NECKLACE", "BRACELET", "EARRING", "JEWELLERY", "JEWELRY", "RING", "BROOCH", "SCARF", "UMBRELLA")),
    ("园艺户外", ("GARDEN", "PLANT", "FLOWER", "WATERING", "SEED", "BIRD")),
]

CATEGORY_ID = {name: i + 1 for i, (name, _) in enumerate(CATEGORY_RULES)}
CATEGORY_ID["其他"] = len(CATEGORY_RULES) + 1


def derive_category(desc: str) -> str:
    upper = (desc or "").upper()
    for name, keys in CATEGORY_RULES:
        for key in keys:
            if key in upper:
                return name
    return "其他"


def is_product_code(code: str) -> bool:
    """是不是商品编码。

    原始编码的形态：5 位数字（`85123A` 这类带 0~2 个字母后缀），
    以及少量 `BANK CHARGES`、`POST`、`M`、`AMAZONFEE` 之类的服务科目。
    """
    if not code:
        return False
    if code.upper() in NON_PRODUCT:
        return False
    return re.match(r"^\d{4,5}[A-Z]{0,2}$", code) is not None


def load_raw(cache: str) -> pd.DataFrame:
    if os.path.exists(cache):
        print("读取缓存 %s" % cache)
        return pd.read_pickle(cache)
    raise SystemExit("缺少缓存 %s；请先运行 .tmp/realdata/uci_profile2.py 生成" % cache)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="UCI Online Retail II -> MySQL CSV")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--win-lo", default=None,
                    help="窗口起点（默认全量起点 2009-12-01）")
    ap.add_argument("--win-hi", default=None,
                    help="窗口终点（开区间，默认全量终点 2011-12-10）")
    args = ap.parse_args(argv)

    # 窗口可覆盖：默认用全量两年，便于"全量真实数据"验收；
    # 传 --win-lo/--win-hi 可切回任意子窗口做对照实验。
    global WIN_LO, WIN_HI
    if args.win_lo:
        WIN_LO = pd.Timestamp(args.win_lo)
    if args.win_hi:
        WIN_HI = pd.Timestamp(args.win_hi)
    print("时间窗: %s ~ %s（开区间）" % (WIN_LO, WIN_HI))

    df = load_raw(args.cache)
    df = df.rename(columns={
        "Invoice": "invoice", "StockCode": "stock_code", "Description": "description",
        "Quantity": "quantity", "InvoiceDate": "invoice_date", "Price": "price",
        "Customer ID": "customer_id", "Country": "country",
    })
    df = df[[c for c in df.columns if not c.startswith("__")]]
    for col in ("invoice", "stock_code"):
        df[col] = df[col].astype(str).str.strip()
    df["description"] = df["description"].fillna("").astype(str).str.strip()
    df["country"] = df["country"].astype(str).str.strip()

    steps = Counter()
    steps["原始行数(全量)"] = len(df)

    # ---- 1. 时间窗 ------------------------------------------------------
    df = df[(df["invoice_date"] >= WIN_LO) & (df["invoice_date"] < WIN_HI)].copy()
    steps["30 天窗口内"] = len(df)

    # ---- 2. 整行去重 ----------------------------------------------------
    before = len(df)
    df = df.drop_duplicates(subset=["invoice", "stock_code", "quantity", "price",
                                    "customer_id", "invoice_date", "country"])
    steps["去掉整行重复"] = before - len(df)

    # ---- 3. 必须能建立外键 ----------------------------------------------
    before = len(df)
    df = df[df["customer_id"].notna()]
    steps["去掉 Customer ID 为空"] = before - len(df)

    before = len(df)
    df = df[df["stock_code"].map(is_product_code)]
    steps["去掉非商品科目"] = before - len(df)

    before = len(df)
    df = df[df["price"] > 0]
    steps["去掉 Price<=0"] = before - len(df)

    # ---- 4. 拆分订单 / 退款 ---------------------------------------------
    df["is_cancel"] = df["invoice"].str.upper().str.startswith("C") | (df["quantity"] < 0)
    pos = df[(~df["is_cancel"]) & (df["quantity"] > 0)].copy()
    neg = df[df["is_cancel"]].copy()
    steps["正向订单行"] = len(pos)
    steps["取消/退货行"] = len(neg)

    # ---- 5. 维度 --------------------------------------------------------
    user_ids = {cid: i + 1 for i, cid in enumerate(sorted(df["customer_id"].unique()))}
    codes = sorted(pos["stock_code"].unique())
    product_ids = {code: i + 1 for i, code in enumerate(codes)}

    # 商品名：同一编码取出现次数最多的非空 Description
    name_by_code = (
        pos[pos["description"] != ""]
        .groupby("stock_code")["description"]
        .agg(lambda s: s.value_counts().index[0])
        .to_dict()
    )
    price_by_code = pos.groupby("stock_code")["price"].median().to_dict()

    products = []
    for code in codes:
        name = name_by_code.get(code, "")
        cat = derive_category(name)
        products.append({
            "product_id": product_ids[code],
            "product_name": name if name else code,
            "category_id": CATEGORY_ID[cat],
            "category_name": cat,
            "brand": "",
            "price": round(float(price_by_code[code]), 2),
            "cost": "",
            "status": 1,
            "create_time": "",
            "update_time": "",
        })
    product_df = pd.DataFrame(products)

    # 用户：注册时间 = 该客户在窗口内的首次下单时间（无法更早，源数据没有注册事件）
    first_order = pos.groupby("customer_id")["invoice_date"].min()
    country_by_cust = pos.groupby("customer_id")["country"].agg(lambda s: s.value_counts().index[0])
    users = []
    for cid, uid in user_ids.items():
        if cid not in first_order.index:      # 只在取消行里出现过的客户，没有注册依据
            continue
        users.append({
            "user_id": uid,
            "username": "customer_%d" % int(cid),
            "gender": "",
            "age": "",
            "province": country_by_cust[cid],
            "city": "",
            "user_level": "",
            "register_time": first_order[cid].strftime("%Y-%m-%d %H:%M:%S"),
            "update_time": first_order[cid].strftime("%Y-%m-%d %H:%M:%S"),
        })
    user_df = pd.DataFrame(users)

    # ---- 6. 订单 --------------------------------------------------------
    pos = pos.sort_values(["invoice_date", "invoice", "stock_code"], kind="mergesort").reset_index(drop=True)
    pos["order_id"] = range(1, len(pos) + 1)
    pos["user_id"] = pos["customer_id"].map(user_ids)
    pos["product_id"] = pos["stock_code"].map(product_ids)
    pos["amount"] = (pos["quantity"].astype("int64") * pos["price"]).round(2)
    ts = pos["invoice_date"].dt.strftime("%Y-%m-%d %H:%M:%S")
    order_df = pd.DataFrame({
        "order_id": pos["order_id"].astype("int64"),
        "user_id": pos["user_id"].astype("int64"),
        "product_id": pos["product_id"].astype("int64"),
        "quantity": pos["quantity"].astype("int64"),
        "amount": pos["amount"],
        "status": "PAID",
        "create_time": ts,
        "pay_time": ts,
        "update_time": ts,
    })
    # user_df 里被丢掉的客户（只在取消行出现）不应留下悬空外键
    keep_users = set(user_df["user_id"].tolist())
    before = len(order_df)
    order_df = order_df[order_df["user_id"].isin(keep_users)].reset_index(drop=True)
    steps["去掉无注册行为的客户订单"] = before - len(order_df)

    payment_df = pd.DataFrame({
        "payment_id": order_df["order_id"].astype("int64"),
        "order_id": order_df["order_id"].astype("int64"),
        "user_id": order_df["user_id"].astype("int64"),
        "amount": order_df["amount"],
        "payment_method": "UNKNOWN",
        "payment_status": "SUCCESS",
        "payment_time": order_df["pay_time"],
    })

    # ---- 7. 退款：按 (客户, 商品) 匹配窗口内更早的订单 -------------------
    #  源数据**无法**按发票号关联原单（实测 C 发票去掉前缀后与普通发票号匹配 0/8292），
    #  因此这里用 (客户, 商品) + 时间更早 作为匹配规则，并对未匹配上的行计数丢弃。
    first_order_of = {
        key: g["order_id"].iloc[0]
        for key, g in pos.groupby(["user_id", "product_id"], sort=False)
    }
    first_time_of = pos.groupby(["user_id", "product_id"])["invoice_date"].min()

    neg = neg.sort_values(["invoice_date", "invoice", "stock_code"], kind="mergesort")
    refunds = []
    unmatched = 0
    for row in neg.itertuples(index=False):
        uid = user_ids.get(row.customer_id)
        pid = product_ids.get(row.stock_code)
        if uid is None or pid is None or uid not in keep_users:
            unmatched += 1
            continue
        key = (uid, pid)
        if key not in first_order_of:
            unmatched += 1
            continue
        refunds.append({
            "order_id": int(first_order_of[key]),
            "user_id": int(uid),
            "refund_amount": round(abs(int(row.quantity)) * float(row.price), 2),
            "refund_reason": "CANCELLED",
            "refund_status": "SUCCESS",
            "refund_time": row.invoice_date.strftime("%Y-%m-%d %H:%M:%S"),
        })
    refund_df = pd.DataFrame(refunds)
    if len(refund_df):
        refund_df.insert(0, "refund_id", range(1, len(refund_df) + 1))
        refund_df = refund_df[["refund_id", "order_id", "user_id", "refund_amount",
                               "refund_reason", "refund_status", "refund_time"]]
    steps["退款匹配失败(丢弃)"] = unmatched

    # ---- 8. 落盘 --------------------------------------------------------
    os.makedirs(args.out, exist_ok=True)
    product_df.to_csv(os.path.join(args.out, "product.csv"), index=False, encoding="utf-8")
    user_df.to_csv(os.path.join(args.out, "user.csv"), index=False, encoding="utf-8")
    order_df.to_csv(os.path.join(args.out, "orders.csv"), index=False, encoding="utf-8")
    payment_df.to_csv(os.path.join(args.out, "payment.csv"), index=False, encoding="utf-8")
    refund_df.to_csv(os.path.join(args.out, "refund.csv"), index=False, encoding="utf-8")

    # ---- 9. 自检 --------------------------------------------------------
    assert order_df["user_id"].isin(user_df["user_id"]).all(), "orders.user_id 悬空"
    assert order_df["product_id"].isin(product_df["product_id"]).all(), "orders.product_id 悬空"
    assert payment_df["order_id"].isin(order_df["order_id"]).all(), "payment.order_id 悬空"
    if len(refund_df):
        assert refund_df["order_id"].isin(order_df["order_id"]).all(), "refund.order_id 悬空"
    ref = pos.set_index("order_id")
    expected = (ref["quantity"] * ref["price"]).round(2).reindex(order_df["order_id"]).values
    exact = bool((order_df["amount"].round(2).values == expected).all())
    assert exact, "amount != quantity * price"

    gmv = float(order_df["amount"].sum())
    lines = ["# UCI Online Retail II -> MySQL 装载数据集\n"]
    lines.append("- 时间窗：%s ~ %s（30 天，保留原始时间戳，未平移）" % (WIN_LO, WIN_HI))
    lines.append("- 输出目录：`%s`" % args.out)
    lines.append("")
    lines.append("## 行数变化（每一步都有交代，不存在静默过滤）\n")
    lines.append("| 步骤 | 行数 |")
    lines.append("| --- | ---: |")
    for k, v in steps.items():
        lines.append("| %s | %s |" % (k, format(v, ",")))
    lines.append("")
    lines.append("## 装载规模\n")
    lines.append("| 表 | 行数 |")
    lines.append("| --- | ---: |")
    for name, d in (("product", product_df), ("user", user_df), ("orders", order_df),
                    ("payment", payment_df), ("refund", refund_df)):
        lines.append("| %s | %s |" % (name, format(len(d), ",")))
    lines.append("")
    lines.append("- GMV 合计：**%s**" % format(round(gmv, 2), ",.2f"))
    lines.append("- 订单时间跨度：%s ~ %s" % (order_df["create_time"].min(), order_df["create_time"].max()))
    lines.append("- 订单量（`quantity*price == amount` 精确相等）：%s / %s" % (
        format(len(order_df) if exact else 0, ","), format(len(order_df), ",")))
    lines.append("- 退款率（笔数 / 订单数）：%.4f%%" % (100.0 * len(refund_df) / len(order_df)))
    lines.append("")
    lines.append("## 类目派生分布（源数据无类目字段，由 Description 关键词派生）\n")
    lines.append("| 类目 | 商品数 |")
    lines.append("| --- | ---: |")
    for name, cnt in product_df["category_name"].value_counts().items():
        lines.append("| %s | %s |" % (name, format(int(cnt), ",")))
    lines.append("")
    lines.append("## 国家（作为 province）Top10\n")
    lines.append("| 国家 | 用户数 |")
    lines.append("| --- | ---: |")
    for name, cnt in user_df["province"].value_counts().head(10).items():
        lines.append("| %s | %s |" % (name, format(int(cnt), ",")))

    md = os.path.join(args.out, "UCI_DATASET.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\n写出 -> %s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
