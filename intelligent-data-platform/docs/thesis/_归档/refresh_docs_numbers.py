#!/usr/bin/env python3
"""把文档里的旧合成数据数字替换为真实数据实测值。

数字来源：docs/thesis/REAL_DATA_FACTS.md（唯一来源）。
两类文件：
  * 前瞻性文档（README、AGENTS、论文、答辩材料……）——直接替换；
  * 历史记录（docs/sprint/*、DEVELOPMENT_LOG.md、DECISIONS.md）——不改数字，
    只在文首补一句"这些是合成数据阶段的实测结果"。
用法：
  python docs/thesis/refresh_docs_numbers.py --dry-run
  python docs/thesis/refresh_docs_numbers.py --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

# (正则, 替换, 说明) —— 顺序敏感：长的、带上下文的放前面
RULES: list[tuple[str, str, str]] = [
    # 金额
    (r"51,890,375\.77", "17,068,016.12", "GMV 旧值"),
    (r"51890375\.77", "17068016.12", "GMV 旧值（无千分位）"),
    # 窗口数：11458/11459 是交易域对账/源表窗口，19643/19644 是流量域
    (r"(?<!\d)11458(?!\d)", "40358", "交易域对账窗口"),
    (r"(?<!\d)11,458(?!\d)", "40,358", "交易域对账窗口（带千分位）"),
    (r"(?<!\d)11459(?!\d)", "40360", "交易域源表窗口"),
    (r"(?<!\d)11,459(?!\d)", "40,360", "交易域源表窗口（带千分位）"),
    (r"(?<!\d)19643(?!\d)", "12957", "流量域对账窗口"),
    (r"(?<!\d)19,643(?!\d)", "12,957", "流量域对账窗口（带千分位）"),
    (r"(?<!\d)19644(?!\d)", "12960", "流量域源表窗口"),
    (r"(?<!\d)19,644(?!\d)", "12,960", "流量域源表窗口（带千分位）"),
    # 零值窗口（交易域）
    (r"(?<!\d)5,?504(?!\d)", "6,088", "交易域零值窗口"),
    (r"(?<!\d)5,?954(?!\d)", "34,272", "交易域非零窗口"),
    # 五表行数连写
    (r"1200/600/6000/5406/254", "5852/4619/776575/776575/16552", "ODS 五表行数"),
    (r"1,200/600/6,000/5,406/254", "5,852/4,619/776,575/776,575/16,552", "ODS 五表行数（带千分位）"),
    # 行为明细行数（必须带量词，避免误伤）
    (r"(?<!\d)20,?000(?=\s*(?:行|条|次)\s*(?:行为|事件|记录))", "10,098,974", "行为明细行数"),
    (r"(?<!\d)20,?000(?=\s*行)", "10,098,974", "行为明细行数"),
    # 订单 / 支付 / 退款（带量词）
    (r"(?<!\d)6,?000(?=\s*(?:笔|单|条)?\s*订单)", "776,575", "订单量"),
    (r"(?<!\d)5,?406(?=\s*(?:笔|条)?\s*(?:支付|payment))", "776,575", "支付笔数"),
    (r"(?<!\d)254(?=\s*(?:笔|条)?\s*退款)", "16,552", "退款笔数"),
]

HEADER_NOTE = (
    "> **历史记录**：本文档记录的是合成数据阶段（2026-09 ~ 2026-10）的实测结果与决策过程，"
    "其中的人数、订单量、窗口数等数字**已不适用于当前版本**。"
    "2026-11 起项目已切换为两个公开真实数据集，最新数字见 "
    "`docs/thesis/REAL_DATA_FACTS.md`。\n\n"
)

FORWARD_FILES = [
    "README.md",
    "AGENTS.md",
    "docs/项目介绍_给同学老师.md",
    "docs/thesis/REPORT_DRAFT.md",
    "docs/thesis/REPORT_OUTLINE.md",
    "docs/thesis/DATA_AND_LIMITATIONS.md",
    "docs/thesis/外部审查包.md",
    "docs/thesis/答辩材料/README.md",
    "docs/thesis/答辩材料/DEFENSE_QA.md",
    "docs/thesis/答辩材料/DEFENSE_DEMO.md",
    "docs/thesis/答辩材料/DEFENSE_SCRIPT.md",
    "docs/thesis/答辩材料/DEFENSE_SLIDES.md",
    "docs/thesis/答辩材料/QA_PREP.md",
    "docs/thesis/求职材料/RESUME_PROJECT.md",
    "docs/thesis/FRONTEND_FINAL_REVIEW.md",
    "docs/thesis/FRONTEND_UI_AUDIT.md",
]

HISTORY_FILES = [
    "DEVELOPMENT_LOG.md",
    "docs/DECISIONS.md",
    # 合成数据阶段的验收/审计记录：改数字会让报告描述一件没发生过的事，
    # 所以保留原文，只在文首加说明，并另写 *_V2 记录真实数据阶段的验收。
    "docs/thesis/答辩材料/FINAL_ACCEPTANCE_REPORT.md",
    "docs/thesis/答辩材料/FINAL_AUDIT_REPORT.md",
    "docs/thesis/EVIDENCE_MATRIX.md",
]


def forward_targets() -> list[Path]:
    return [ROOT / p for p in FORWARD_FILES if (ROOT / p).exists()]


def history_targets() -> list[Path]:
    out = [ROOT / p for p in HISTORY_FILES if (ROOT / p).exists()]
    if (DOCS / "sprint").is_dir():
        out.extend(sorted((DOCS / "sprint").glob("*.md")))
    return out


def apply_rules(text: str) -> tuple[str, list[tuple[str, int]]]:
    hits: list[tuple[str, int]] = []
    for pattern, repl, label in RULES:
        text, n = re.subn(pattern, repl, text)
        if n:
            hits.append((label, n))
    return text, hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (args.apply or args.dry_run):
        args.dry_run = True

    total = 0
    for path in forward_targets():
        text = path.read_text(encoding="utf-8")
        new, hits = apply_rules(text)
        if not hits:
            continue
        n = sum(h[1] for h in hits)
        total += n
        print(f"{n:4d}  {path.relative_to(ROOT)}")
        for label, c in hits:
            print(f"        {label}: {c}")
        if args.apply and new != text:
            path.write_text(new, encoding="utf-8")

    print(f"\n合计替换 {total} 处（{'已写入' if args.apply else 'dry-run，未写入'}）")

    if args.apply:
        added = 0
        for path in history_targets():
            text = path.read_text(encoding="utf-8")
            if "历史记录" in text[:400]:
                continue
            path.write_text(HEADER_NOTE + text, encoding="utf-8")
            added += 1
        print(f"历史记录文件加说明头：{added} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
