#!/usr/bin/env python3
"""对账口径的精确性修补：
1) 凡是一行里同时出现"对账窗口数 40358"与"全量 GMV 17,068,016.12"的，把 GMV 改成
   对账区间内的合计 17,067,427.47（差额 588.65 来自 TAIL_GUARD_MINUTES = 3）；
2) 把旧的批次号换成当前真实数据批次的批次号。
只改前瞻性文档；历史记录（sprint/*、DEVELOPMENT_LOG、DECISIONS、两份旧验收报告、EVIDENCE_MATRIX）不动。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FILES = [
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
]

WINDOW_RE = re.compile(r"40,?358")
GMV_OLD = "17,068,016.12"
GMV_SCOPE = "17,067,427.47"

BATCH_MAP = {
    # 旧合成数据批次号 → 当前真实数据批次号
    "reconcile_20260927_041532": "reconcile_20261111_001950",
    "reconcile_20261003_033544": "reconcile_20261111_001950",
}


def main() -> int:
    total_gmv = 0
    total_batch = 0
    for rel in FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        out_lines = []
        changed_gmv = 0
        for line in text.splitlines(keepends=True):
            if WINDOW_RE.search(line) and GMV_OLD in line and GMV_SCOPE not in line:
                line = line.replace(GMV_OLD, GMV_SCOPE)
                changed_gmv += 1
            out_lines.append(line)
        new = "".join(out_lines)
        for old, cur in BATCH_MAP.items():
            if old in new:
                total_batch += new.count(old)
                new = new.replace(old, cur)
        if changed_gmv:
            total_gmv += changed_gmv
            print(f"{changed_gmv:3d} 行 GMV 改为对账区间合计  {rel}")
        if new != text:
            path.write_text(new, encoding="utf-8")
    print(f"\nGMV 口径修正 {total_gmv} 行；批次号替换 {total_batch} 处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
