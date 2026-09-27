"""语料装载与分块（Sprint 9 检索的知识底座）。

## 语料从哪来

| 来源 | 内容 | 取法 |
| --- | --- | --- |
| `sql/metadata/metrics.md` | 指标口径（**唯一权威**）、通用约定 | 随代码分发，进程内读文件 |
| `sql/metadata/kafka_topics.md` | 事件格式与事件类型枚举 | 随代码分发，进程内读文件 |
| `services/agent/knowledge/layering.md` | 数仓分层与两条链路的粒度差异 | 随代码分发，进程内读文件 |
| `GET /meta/tables` | 表结构（字段/类型/注释/所属库） | **经既有只读接口取**，进程内按 TTL 缓存 |

## 为什么读文件不算"持有数据库凭据"

`AGENTS.md` §10.1 第 1 条约束的是「Agent 不允许直接拥有数据库管理员权限」。
RAG 语料是**知识**，不是**凭据**：这些文件是仓库里人写给人的说明文档，
Agent 进程里**依然没有任何数据库连接信息**，取数依然只经 `POST /query`。
把这条写清楚，是为了避免后来者把"读文档"与"读数据库"混为一谈 ——
两者的安全含义完全不同。

## 为什么分块要分到"条目级"

若按整篇文档检索，命中"`metrics.md`"这句话提供的信息量几乎为零：
口径文档里有 30 多个指标，用户问 GMV 时我们必须能指出**是哪一行**。
因此：

- markdown 表格**逐行**成块（一个指标一行 → 一个 Doc）；
- 表结构**逐表**成块；
- 其余正文按标题分段成块。

每个 Doc 都带 `source` 与 `line`（起始行号），让回答里的引用**可以当场翻回原文核对**
（`sql/metadata/metrics.md:33`），而不是只写一个文件名。
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .timeutil import now_iso

__all__ = ["Doc", "Corpus", "load_corpus", "parse_markdown_table"]

# 表格分隔行：| --- | :---: | --- |
_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True)
class Doc:
    """一条可检索的知识条目。"""

    doc_id: str
    title: str
    text: str
    source: str
    line: int
    kind: str  # metric | convention | table | topic | layering

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "source": self.source,
            "line": self.line,
            "kind": self.kind,
            "text": self.text,
        }

    def citation(self) -> str:
        """人类可读的引用（回答与审计都用它）。"""
        return f"{self.source}:{self.line}"


def parse_markdown_table(text: str) -> list[tuple[int, list[str]]]:
    """抽取 markdown 表格的**数据行**（表头与分隔行都被剔除）。

    返回 `[(行号, [单元格...]), ...]`；行号是**原文中的 1-based 行号**，
    用于让引用可点回原文。

    `header=True` 时把紧跟在分隔行（`| --- |`）之前的那一行也剔掉 ——
    这是 markdown 表格的表头。做在这里而不是让每个调用方各自判断：
    表头行的形态与数据行**完全一样**（也是 `| a | b |`），
    只有"它在分隔行上一行"这个位置信息能识别它，
    而调用方各自实现一遍必然有人漏掉（漏掉的后果是把"指标 | 字段名"
    当成一条口径条目装进语料）。

    为什么自己解析而不上 `pandas` / `markdown`：
        语料是我们自己维护的、格式受控的 markdown 表格，
        为它引入一个解析库属于"为一件小事新增依赖"（AGENTS.md §4.1 第 3 条）。
    """
    rows: list[tuple[int, list[str]]] = []

    for index, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line.startswith("|"):
            continue
        if _SEPARATOR_RE.match(line):
            # 紧邻的上一条数据行就是表头，撤掉它
            if rows and rows[-1][0] == index - 1:
                rows.pop()
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or all(not cell for cell in cells):
            continue
        rows.append((index, cells))

    return rows


# ------------------------------------------------------------
# 单篇 markdown 的装载
# ------------------------------------------------------------
def _load_metrics(text: str, source: str) -> list[Doc]:
    """口径字典：表格逐行成块（一个指标 = 一个 Doc）。

    表格列的含义（见 metrics.md 第 2 节表头）：
        指标 | 字段名 | 口径 | 计算方式 | 所在表

    第 1 节（通用约定）的表结构不同（约定 | 取值 | 说明），单独识别，
    否则会把"时间语义 / 事件时间"当成指标。

    !! 为什么同名指标要合并 !!
        `gmv` 在第 2 节（交易域）和第 4 节（类目域）各有一行，`doc_id` 相同。
        若各成一块，同一个 doc_id 会出现两条命中，且**类目版本的标题
        恰好也含 `gmv`**，会与交易域版本争抢第一名 ——
        用户问「GMV 怎么算」拿到的是类目口径，属于选错口径。
        合并成一条（正文拼接）后：交易域口径先出现（原文顺序），
        类目口径作为补充仍然可检索、可引用，且不会有重复来源。
    """
    merged: dict[str, Doc] = {}
    order: list[str] = []

    for line_no, cells in parse_markdown_table(text):
        if len(cells) < 5:
            continue
        name, field_name, meaning, calc, table = cells[0], cells[1], cells[2], cells[3], cells[4]
        # 表头行
        if name in ("指标", "字段名") or field_name == "字段名":
            continue
        if not field_name:
            continue
        key = field_name.strip("`")
        doc_id = f"metric:{key}"
        piece = (
            f"指标 {name}，字段名 {key}，口径：{meaning}，"
            f"计算方式：{calc}，所在表：{table}"
        )
        if doc_id in merged:
            existing = merged[doc_id]
            merged[doc_id] = Doc(
                doc_id=existing.doc_id,
                title=existing.title,
                text=f"{existing.text}\n另有同类指标：{piece}",
                source=existing.source,
                line=existing.line,
                kind=existing.kind,
            )
            continue
        merged[doc_id] = Doc(
            doc_id=doc_id,
            title=f"{name}（{key}）",
            text=piece,
            source=source,
            line=line_no,
            kind="metric",
        )
        order.append(doc_id)

    docs = [merged[doc_id] for doc_id in order]

    # 第 1 节通用约定：3 列表格
    for line_no, cells in parse_markdown_table(text):
        if len(cells) != 3:
            continue
        name, value, meaning = cells
        if name in ("约定",) or value == "取值":
            continue
        if not name:
            continue
        docs.append(
            Doc(
                doc_id=f"convention:{name}",
                title=f"通用约定：{name}",
                text=f"通用约定 {name}：取值 {value}，说明：{meaning}",
                source=source,
                line=line_no,
                kind="convention",
            )
        )
    return docs


def _load_kafka_topics(text: str, source: str) -> list[Doc]:
    """Kafka 事件格式：逐 (Topic, 事件类型) 成块，并保留字段说明。"""
    docs: list[Doc] = []
    current_topic = ""
    current_line = 0
    for line_no, raw in enumerate(text.splitlines(), start=1):
        heading = _HEADING_RE.match(raw.strip())
        if heading:
            title = heading.group(2).strip()
            match = re.match(r"^`?(\w+_event)`?", title)
            if match:
                current_topic = match.group(1)
                current_line = line_no
            continue
        if not current_topic:
            continue
        # 事件类型枚举行：| `ORDER_CREATED` | 订单创建 |
        cells_match = re.match(r"^\|\s*`([A-Z_]+)`\s*\|\s*(.+?)\s*\|", raw.strip())
        if cells_match:
            event_type, meaning = cells_match.group(1), cells_match.group(2)
            docs.append(
                Doc(
                    doc_id=f"topic:{current_topic}:{event_type}",
                    title=f"{current_topic} 事件类型 {event_type}",
                    text=f"Kafka 主题 {current_topic} 的事件类型 {event_type}：{meaning}",
                    source=source,
                    line=line_no,
                    kind="topic",
                )
            )

    # Topic 列表（第 1 节）：| `order_event` | 3 | 1 | 订单创建 / 状态变化 |
    for line_no, cells in parse_markdown_table(text):
        if len(cells) != 4:
            continue
        topic_cell = cells[0].strip("`")
        if not topic_cell.endswith("_event"):
            continue
        docs.append(
            Doc(
                doc_id=f"topic:{topic_cell}",
                title=f"Kafka Topic {topic_cell}",
                text=(
                    f"Kafka 主题 {topic_cell}（分区 {cells[1]}，副本 {cells[2]}）：{cells[3]}"
                ),
                source=source,
                line=line_no,
                kind="topic",
            )
        )
    return docs


def _load_sections(text: str, source: str, kind: str) -> list[Doc]:
    """按 markdown 标题分段成块（用于分层说明等叙述性文档）。"""
    docs: list[Doc] = []
    lines = text.splitlines()
    starts: list[tuple[int, str, int]] = []  # (行号, 标题, 级别)
    for index, raw in enumerate(lines, start=1):
        match = _HEADING_RE.match(raw.strip())
        if match:
            starts.append((index, match.group(2).strip(), len(match.group(1))))

    for position, (line_no, title, level) in enumerate(starts):
        end = starts[position + 1][0] - 1 if position + 1 < len(starts) else len(lines)
        body = "\n".join(lines[line_no:end]).strip()
        # 只保留自身有内容的段（一级标题下面往往直接是子标题）
        if not body:
            continue
        if level == 1:
            continue
        docs.append(
            Doc(
                doc_id=f"{kind}:{title}",
                title=title,
                text=f"{title}\n{body}",
                source=source,
                line=line_no,
                kind=kind,
            )
        )
    return docs


# ------------------------------------------------------------
# 表结构（经只读接口，非数据库）
# ------------------------------------------------------------
def _table_docs(payload: list[dict[str, Any]]) -> list[Doc]:
    docs: list[Doc] = []
    for entry in payload:
        table = str(entry.get("table", ""))
        if not table:
            continue
        database = str(entry.get("database", ""))
        layer = str(entry.get("layer", ""))
        comment = str(entry.get("comment", ""))
        columns = entry.get("columns") or []
        listing = "；".join(
            f"{col.get('name')}({col.get('type')}{'，' + str(col.get('comment')) if col.get('comment') else ''})"
            for col in columns
        )
        docs.append(
            Doc(
                doc_id=f"table:{table}",
                title=f"表 {database}.{table}（{layer} 层）",
                text=(
                    f"表结构 {database}.{table}，分层 {layer}，表说明：{comment or '无'}。"
                    f"字段：{listing}"
                ),
                source="GET /meta/tables",
                line=0,
                kind="table",
            )
        )
    return docs


# ------------------------------------------------------------
# 语料容器
# ------------------------------------------------------------
@dataclass
class Corpus:
    """一次装载得到的全部知识条目 + 装载过程的真实状态。

    `channels` 刻意记录"表结构这一路是否成功"：
    检索质量依赖它，而**静默降级**（少了一路语料却假装完整）正是
    AGENTS.md §3「成功信号不可信」要防的事。
    """

    docs: list[Doc] = field(default_factory=list)
    channels: dict[str, Any] = field(default_factory=dict)
    loaded_at: str = ""

    def by_kind(self, kind: str) -> list[Doc]:
        return [doc for doc in self.docs if doc.kind == kind]

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for doc in self.docs:
            result[doc.kind] = result.get(doc.kind, 0) + 1
        return result


# 语料文件（仓库内相对路径；随代码分发，Agent 直接读）
CORPUS_FILES: tuple[tuple[str, str], ...] = (
    ("sql/metadata/metrics.md", "metrics"),
    ("sql/metadata/kafka_topics.md", "topics"),
    ("services/agent/knowledge/layering.md", "layering"),
)


def fetch_table_metadata(settings: Settings, timeout: int = 10) -> tuple[list[Doc], str]:
    """经既有只读接口取表结构。返回 (文档, 状态说明)。

    **为什么不直连 information_schema**：Agent 进程里没有数据库凭据，
    也不允许有。表结构属于"可经只读接口获得"的信息，
    走接口还能顺带保证"表结构与权限白名单来自同一个源头"。

    失败时返回空列表 + 失败原因（不抛异常、不假装成功）：
    检索会退化为"只有口径与分层文档"，并在 `/api/retrieve` 的
    `channels.live_tables` 里如实标注，让调用方知道少了一路语料。
    """
    url = f"{settings.data_api_base}/meta/tables"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            import json

            body = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return [], f"不可达：{type(exc).__name__}: {exc}"

    rows = body.get("data") or []
    if not isinstance(rows, list) or not rows:
        return [], "接口返回为空（表结构语料缺失）"
    return _table_docs(rows), f"ok（{len(rows)} 张表）"


def load_corpus(settings: Settings, include_live_tables: bool = True) -> Corpus:
    """装载全部语料。缺哪个文件就跳过哪个，并把情况记进 `channels`。"""
    corpus = Corpus(loaded_at=now_iso())

    for relative, kind in CORPUS_FILES:
        path = settings.repo_root / relative
        if not path.is_file():
            corpus.channels[relative] = "缺失"
            continue
        text = path.read_text(encoding="utf-8")
        if kind == "metrics":
            docs = _load_metrics(text, relative)
        elif kind == "topics":
            docs = _load_kafka_topics(text, relative)
        else:
            docs = _load_sections(text, relative, "layering")
        corpus.docs.extend(docs)
        corpus.channels[relative] = f"ok（{len(docs)} 条）"

    if include_live_tables:
        table_docs, status = fetch_table_metadata(settings)
        corpus.docs.extend(table_docs)
        corpus.channels["live_tables"] = status
    else:
        corpus.channels["live_tables"] = "已跳过"

    return corpus
