"""指标口径字典的结构化读取。

**唯一权威来源是 `sql/metadata/metrics.md`**（Markdown 表格）。
这里不重新定义任何指标，而是把文档里的表格解析成结构化数据供接口与前端使用。

为什么选择"解析文档"而不是"在代码里再抄一份"：
    口径一旦有两份副本，迟早会不一致 —— 实时链路改了、接口没改，
    前端展示的口径与真实计算方式就对不上，Agent 引用时更是错的。
    因此这里把文档当作数据源；解析不到指标就直接报错（宁可接口 500，
    也不要返回一份"看起来对"的口径）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MetricDefinition",
    "Convention",
    "MetricsDoc",
    "load_metrics_doc",
]

# 域标题：`## 2. 交易域指标` / `### 3.1 流量域指标`
# 注意 `\.?`：编号后面那个点必须显式吃掉，否则 `\s*` 匹配不到 `.`，
# 整条正则永远不命中（实测踩过：所有指标的 domain 都退化成"通用"）。
_DOMAIN_RE = re.compile(r"^#{2,4}\s*\d+(?:\.\d+)?\.?\s*([\u4e00-\u9fa5]+)域指标")
_VERSION_RE = re.compile(r"版本：(V[\d.]+)")
_UPDATED_RE = re.compile(r"更新日期：([\d-]+)")
_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")

_METRIC_HEADER = ["指标", "字段名", "口径", "计算方式"]
_CONVENTION_HEADER = ["约定", "取值"]


@dataclass
class MetricDefinition:
    """一个指标的口径定义。"""

    domain: str
    metric: str
    field: str
    definition: str
    formula: str
    table: str
    null_policy: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain,
            "metric": self.metric,
            "field": self.field,
            "definition": self.definition,
            "formula": self.formula,
            "table": self.table,
            "null_policy": self.null_policy,
        }


@dataclass
class Convention:
    """通用约定（时间语义 / 窗口 / 空值约定等）。"""

    name: str
    value: str
    note: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value, "note": self.note}


@dataclass
class MetricsDoc:
    """解析后的指标口径字典。"""

    metrics: list[MetricDefinition] = field(default_factory=list)
    conventions: list[Convention] = field(default_factory=list)
    version: str = ""
    updated_at: str = ""
    source_path: str = ""

    def by_field(self) -> dict[str, MetricDefinition]:
        """按字段名索引口径定义。

        同一字段名可能在多个域出现（例如 `gmv` 同时存在于交易域与类目域）。
        这里**保留文档中先出现的那个**（交易域在前，是主口径），
        避免后来的类目口径把主口径覆盖掉。
        """
        index: dict[str, MetricDefinition] = {}
        for item in self.metrics:
            index.setdefault(item.field, item)
        return index

    def definitions_for(self, fields: list[str]) -> list[dict[str, str]]:
        """取若干字段的口径说明（用于接口响应里的 source.metric_definitions）。"""
        index = self.by_field()
        return [index[name].as_dict() for name in fields if name in index]


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _null_policy(metric: str, formula: str) -> str:
    """按口径文本判断空值约定（与 metrics.md 第 1 节保持一致）。"""
    if "分母" in formula or "率" in metric:
        return "分母为 0 时 NULL"
    return "无事件补 0"


def load_metrics_doc(path: Path) -> MetricsDoc:
    """读取并解析指标口径文档；解析不到指标时抛 ValueError。

    解析策略（Markdown 表格是"连续块"）：
        遇到表格块的第一行 → 判定这块表是什么（指标表 / 约定表 / 其它）；
        后续行按该判定处理，直到表格块结束。
        这样即使文档里插入了别的表格，也不会被误当成指标行。
    """
    if not path.is_file():
        raise ValueError(f"指标口径文档不存在：{path}")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    doc = MetricsDoc(source_path=str(path))
    if match := _VERSION_RE.search(text):
        doc.version = match.group(1)
    if match := _UPDATED_RE.search(text):
        doc.updated_at = match.group(1)

    domain = "通用"
    table_kind: str | None = None   # None=不在表格中 / "metrics" / "conventions" / "other"
    seen_header = False
    last_table = ""                 # 同一张表内的"同上"要还原成真实表名

    for line in lines:
        stripped = line.strip()

        # 1) 域标题：切换当前域，并结束上一张表
        if match := _DOMAIN_RE.match(line):
            domain = match.group(1)
            table_kind, seen_header = None, False
            continue

        # 2) 分隔行（|---|---|）：表格继续，但不是表头
        if _SEPARATOR_RE.match(stripped):
            seen_header = True
            continue

        # 3) 普通行：表格结束
        if not (stripped.startswith("|") and stripped.endswith("|")):
            table_kind, seen_header = None, False
            continue

        cells = _split_row(line)

        # 4) 表格块的第一行是表头 —— 决定这块表的类型
        if not seen_header:
            if cells[:4] == _METRIC_HEADER:
                table_kind = "metrics"
            elif cells[:2] == _CONVENTION_HEADER:
                table_kind = "conventions"
            else:
                table_kind = "other"
            seen_header = True
            continue

        # 5) 数据行
        if table_kind == "metrics" and len(cells) >= 5:
            metric, field_name, definition, formula, table = cells[:5]
            table = table.strip("`")
            # 文档里后续行常用"同上"表示与上一行同一张表：
            # 若原样保留，口径接口给出的"所在表"就没法用于血缘展示。
            if table in ("", "同上", "-", "—"):
                table = last_table
            else:
                last_table = table
            doc.metrics.append(
                MetricDefinition(
                    domain=domain,
                    metric=metric,
                    field=field_name.strip("`"),
                    definition=definition,
                    formula=formula,
                    table=table,
                    null_policy=_null_policy(metric, formula),
                )
            )
        elif table_kind == "conventions" and len(cells) >= 2:
            doc.conventions.append(
                Convention(name=cells[0], value=cells[1], note=cells[2] if len(cells) > 2 else "")
            )

    if not doc.metrics:
        raise ValueError(f"未从 {path} 解析出任何指标定义（文档结构可能已变化）")
    return doc
