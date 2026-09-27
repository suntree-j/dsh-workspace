"""前端「数据质量校验清单」镜像的一致性测试（Sprint 13 前端收口）。

## 这个测试防的是什么

`services/web/app.js` 里有一份 `QUALITY_GROUPS` —— 它是
`infrastructure/quality/checks.conf` 的**只读镜像**，用来在 `#/quality` 页
展示校验清单（标题 / 域 / 执行引擎）。

之所以要做成镜像而不是从接口取：
    数据服务**没有**质量校验的只读接口，而本次前端改造的硬约束是
    "不新增后端功能、不修改 API contract"。所以只能在前端放一份静态清单。

代价是**两份数据可能漂移**：checks.conf 加了校验、前端清单没跟上，
页面就会显示一份"看起来正确但已经过期"的清单 —— 这类问题不会报错，
只会让人看到错误的条数与标题。

## 判据

逐项比对 **id 与 title**（顺序无关，因为两边分组方式不同），并核对：
  * 条数一致
  * id 集合一致（不缺失、不多余）
  * 每个 id 的 title 逐字一致
  * domain 一致
  * 前端标为 spark 引擎的那条，在 checks.conf 里也确实是 spark
    （页面上"spark 那条默认不跑"的提示依赖这个标记）

## 为什么标题要逐字比

"校验项名称（会打印出来）"是 checks.conf 自己声明的用途 —— 它会出现在
CLI 输出里。页面上显示的标题如果与 CLI 输出不同，两处对不上，
排查时会以为看的是两件事。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKS_CONF = REPO_ROOT / "infrastructure" / "quality" / "checks.conf"
APP_JS = REPO_ROOT / "services" / "web" / "app.js"


# ----------------------------------------------------------------------
# 解析 checks.conf
# ----------------------------------------------------------------------
def parse_checks_conf() -> dict[str, dict[str, str]]:
    """返回 {id: {title, domain, severity, engine}}。

    格式（见 checks.conf 顶部说明）：每条是一个 `[check.<id>]` 段，
    段内是 `key<空白>value` 行 —— **不是** `key = value`。
    """
    text = CHECKS_CONF.read_text(encoding="utf-8")
    blocks = re.split(r"^\[check\.", text, flags=re.M)[1:]
    out: dict[str, dict[str, str]] = {}
    for block in blocks:
        cid = block.split("]", 1)[0].strip()
        fields: dict[str, str] = {}
        for key in ("title", "domain", "severity", "engine"):
            m = re.search(rf"^{key}\s+(.*)$", block, re.M)
            fields[key] = m.group(1).strip() if m else ""
        out[cid] = fields
    return out


# ----------------------------------------------------------------------
# 解析 app.js 里的 QUALITY_GROUPS
# ----------------------------------------------------------------------
def parse_quality_groups() -> list[dict[str, str]]:
    """从 app.js 的 QUALITY_GROUPS 里抽出 {id, title, domain, engine}。

    刻意用**正则**而不是执行 JS：测试不应该为了读一份静态清单而
    引入 node 依赖（本项目的测试只跑 python）。
    正则的锚点是清单的固定写法 `{ id: '...', title: '...' }`，
    如果哪天写法变了，这个测试会因为"抽不到项"而失败 ——
    那正是希望被发现的时刻，而不是静默跳过。
    """
    src = APP_JS.read_text(encoding="utf-8")
    start = src.find("var QUALITY_GROUPS")
    assert start >= 0, "app.js 里找不到 QUALITY_GROUPS（前端清单被删了？）"
    # 清单以 `];` 结束
    end = src.find("\n  ];", start)
    assert end > start, "QUALITY_GROUPS 的结束标记（\\n  ];）找不到了"
    block = src[start:end]

    items: list[dict[str, str]] = []
    # 先按 domain 分组切块，再在每块里抽 checks
    for gm in re.finditer(r"domain:\s*'([a-z]+)'", block):
        gdomain = gm.group(1)
        # 找该分组到下一个 domain 之间的片段
        nxt = block.find("domain:", gm.end())
        seg = block[gm.end(): nxt if nxt > 0 else len(block)]
        for cm in re.finditer(
            r"\{\s*id:\s*'([^']+)'\s*,\s*title:\s*'([^']+)'"
            r"(?:\s*,\s*engine:\s*'([^']+)')?\s*\}",
            seg,
        ):
            items.append(
                {
                    "id": cm.group(1),
                    "title": cm.group(2),
                    "domain": gdomain,
                    "engine": cm.group(3) or "doris",
                }
            )
    return items


conf = parse_checks_conf()
front = parse_quality_groups()
front_by_id = {i["id"]: i for i in front}


@pytest.mark.unit
def test_checks_conf_parsed() -> None:
    """先证明解析器真的读到了东西（21~30 条之间是合理量级）。"""
    assert 20 <= len(conf) <= 40, f"checks.conf 解析出 {len(conf)} 条，量级不对，解析器可能失效"


@pytest.mark.unit
def test_frontend_manifest_parsed() -> None:
    """同样先证明前端清单被正确抽取（避免"两边都解析成空"的假通过）。"""
    assert len(front) >= 20, f"前端清单只抽到 {len(front)} 条，抽取逻辑可能失效"


@pytest.mark.unit
def test_counts_match() -> None:
    assert len(front) == len(conf), (
        f"条数不一致：checks.conf {len(conf)} 条，前端清单 {len(front)} 条。\n"
        "处置：同步 services/web/app.js 的 QUALITY_GROUPS（它是 checks.conf 的镜像）。"
    )


@pytest.mark.unit
def test_ids_match() -> None:
    missing = sorted(set(conf) - set(front_by_id))
    extra = sorted(set(front_by_id) - set(conf))
    assert not missing and not extra, (
        f"id 集合不一致。\n  前端缺少：{missing}\n  前端多出：{extra}\n"
        "处置：QUALITY_GROUPS 必须与 checks.conf 逐条对应，不多不少。"
    )


@pytest.mark.unit
@pytest.mark.parametrize("cid", sorted(conf))
def test_title_matches(cid: str) -> None:
    """标题逐字一致 —— 它会出现在 CLI 输出里，两处不同会让人以为是两件事。"""
    assert cid in front_by_id, f"{cid} 不在前端清单里"
    assert front_by_id[cid]["title"] == conf[cid]["title"], (
        f"{cid} 的标题不一致：\n"
        f"  checks.conf : {conf[cid]['title']}\n"
        f"  前端清单    : {front_by_id[cid]['title']}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("cid", sorted(conf))
def test_domain_matches(cid: str) -> None:
    """域一致 —— 它决定这条校验显示在页面的哪个分组下。"""
    assert cid in front_by_id, f"{cid} 不在前端清单里"
    assert front_by_id[cid]["domain"] == conf[cid]["domain"], (
        f"{cid} 的 domain 不一致：checks.conf={conf[cid]['domain']}，"
        f"前端={front_by_id[cid]['domain']}"
    )


@pytest.mark.unit
def test_spark_engine_flag_matches() -> None:
    """标了 spark 的那几条必须与 checks.conf 一致。

    页面用这个标记提示"需要读 Iceberg（约 1 GB 内存），默认不跑"。
    标错会让人误以为某条校验默认会执行（或反之），属实质误导。
    """
    conf_spark = {cid for cid, f in conf.items() if f["engine"] == "spark"}
    front_spark = {cid for cid, f in front_by_id.items() if f["engine"] == "spark"}
    assert front_spark == conf_spark, (
        f"spark 引擎标记不一致：\n"
        f"  checks.conf 标为 spark：{sorted(conf_spark)}\n"
        f"  前端标为 spark       ：{sorted(front_spark)}"
    )
