"""检索接口层（Sprint 9）：`Retriever.retrieve(query) -> docs`。

## 为什么要有这一层（而不是直接调 BM25）

`docs/DECISIONS.md` ⏳7 明确要求：

> 把检索抽象成一个接口（`retrieve(query) -> docs`），先实现词法后端；
> 日后要换向量后端只改这一处，不动 Agent 逻辑。

因此本模块**只做三件事**，把"检索怎么实现"与"检索怎么被使用"隔开：

1. **持有语料与后端**：装载 [`corpus`](corpus.md) → 建 [`lexical`](lexical.md) 索引；
2. **缓存**：语料文件（`metrics.md` / `kafka_topics.md` / `layering.md`）改了不必重启服务，
   但也不至于每次提问都重新解析与重连接口；
3. **给出结论形状**：命中（含来源与行号）+ 生效的同义词 + 语料真实装载状态。

## 缓存键为什么含"文件 mtime + 表结构 TTL"

- **文件 mtime**：口径文档是人工维护的，改完就应生效（与数据服务 `/meta/metrics`
  的 mtime 缓存是同一套理由）；
- **表结构 TTL（10 分钟）**：它来自接口。表结构几乎不变，但**不能永不刷新** ——
  否则新增表后 Agent 会长期"看不见"它，而且这种缺失是静默的。
  TTL 到期后**异步失败也不影响本次检索**：沿用旧索引，但把失败写进 `channels`。

## 失败时的行为（"成功信号不可信"）

若表结构接口不可达，检索**仍可用**（口径与分层文档还在），但：

- `channels["live_tables"]` 会写明失败原因；
- 接口响应里 `degraded=true`，调用方据此知道"少了一路语料"。

绝不静默降级 —— 少了一路语料却假装完整，正是最难发现的故障。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .corpus import CORPUS_FILES, Corpus, load_corpus
from .lexical import LexicalIndex, ScoredDoc, describe_synonym_hits, iter_kinds, load_synonyms

__all__ = ["RetrievalResult", "Retriever", "build_retriever"]

# 表结构语料的刷新间隔（秒）。表结构几乎不变，10 分钟足以避免"新增表长期不可见"。
TABLE_TTL_SECONDS = 600

SYNONYM_FILE = "services/agent/knowledge/synonyms.json"


@dataclass
class RetrievalResult:
    """一次检索的完整结论。"""

    query: str
    hits: list[ScoredDoc] = field(default_factory=list)
    expanded_terms: list[dict[str, str]] = field(default_factory=list)
    docs_total: int = 0
    kinds: list[str] = field(default_factory=list)
    channels: dict[str, Any] = field(default_factory=dict)
    backend: str = "lexical-bm25"
    degraded: bool = False
    elapsed_ms: int = 0

    def sources(self) -> list[dict[str, Any]]:
        """给回答用的来源清单（不含正文，避免响应过长）。"""
        return [
            {
                "doc_id": hit.doc.doc_id,
                "title": hit.doc.title,
                "kind": hit.doc.kind,
                "source": hit.doc.source,
                "line": hit.doc.line,
                "citation": hit.doc.citation(),
                "score": round(hit.score, 4),
                "matched_terms": hit.matched_terms,
            }
            for hit in self.hits
        ]

    def as_dict(self, include_text: bool = True) -> dict[str, Any]:
        hits = [hit.as_dict() for hit in self.hits]
        if not include_text:
            for item in hits:
                item.pop("text", None)
        return {
            "query": self.query,
            "backend": self.backend,
            "hits": hits,
            "expanded_terms": self.expanded_terms,
            "docs_total": self.docs_total,
            "kinds": self.kinds,
            "channels": self.channels,
            "degraded": self.degraded,
            "elapsed_ms": self.elapsed_ms,
        }

    def as_context(self, max_docs: int = 5, max_chars: int = 2400) -> str:
        """把命中渲染成给 LLM 看的上下文（**带来源标注**）。

        来源标注不是装饰：提示词里明确要求"引用口径必须写出来源"，
        而模型只能引用它看得见的东西。没有 `[来源 file:line]`，
        回答里的引用就只能靠模型编。
        """
        if not self.hits:
            return (
                "（检索没有命中任何口径/表结构文档。"
                "请如实说明语料中没有相关口径，**不要**凭常识给出公式。）"
            )

        blocks: list[str] = []
        budget = max_chars
        for hit in self.hits[:max_docs]:
            block = (
                f"[来源 {hit.doc.citation()}｜{hit.doc.kind}｜{hit.doc.title}]\n{hit.doc.text}"
            )
            if len(block) > budget:
                block = block[:budget] + "…（已截断）"
            blocks.append(block)
            budget -= len(block)
            if budget <= 0:
                break

        header = ""
        if self.expanded_terms:
            pairs = "；".join(
                f"{item['phrase']}→{'/'.join(item['terms'])}" for item in self.expanded_terms
            )
            header = f"（同义词扩展生效：{pairs}）\n"
        return header + "\n\n".join(blocks)


class Retriever:
    """检索门面：语料 + 词法后端 + 缓存。"""

    # 是否经接口装载"表结构"这一路语料。
    #
    # 为什么做成实例属性而不是构造参数：
    #   单元测试（AGENTS.md §8.2 要求**不得依赖外部服务**）必须能关掉它 ——
    #   否则 `pytest -m unit` 会在没有数据服务的机器上尝试 HTTP 请求，
    #   把"检索逻辑有没有写对"和"数据服务在不在"混成一个失败。
    #   生产路径永远为 True（构造后不改）。
    include_live_tables: bool = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._corpus: Corpus | None = None
        self._index: LexicalIndex | None = None
        self._signature: tuple[Any, ...] = ()
        self._tables_at: float = 0.0
        self._synonym_path = settings.repo_root / SYNONYM_FILE

    # --------------------------------------------------------
    # 语料与索引
    # --------------------------------------------------------
    def _file_signature(self) -> tuple[Any, ...]:
        parts: list[Any] = []
        for relative, _kind in CORPUS_FILES:
            path = self.settings.repo_root / relative
            try:
                parts.append((relative, path.stat().st_mtime_ns, path.stat().st_size))
            except OSError:
                parts.append((relative, None, None))
        try:
            parts.append((SYNONYM_FILE, self._synonym_path.stat().st_mtime_ns))
        except OSError:
            parts.append((SYNONYM_FILE, None))
        return tuple(parts)

    def _needs_reload(self) -> bool:
        if self._index is None or self._synonym_path is None:
            return True
        now = time.monotonic()
        if now - self._tables_at > TABLE_TTL_SECONDS:
            return True
        return self._file_signature() != self._signature

    def load(self, force: bool = False) -> None:
        """（重新）装载语料并重建索引。"""
        if not force and not self._needs_reload():
            return

        synonyms = load_synonyms(self._synonym_path)
        corpus = load_corpus(self.settings, include_live_tables=self.include_live_tables)
        self._corpus = corpus
        self._index = LexicalIndex.build(corpus.docs, synonyms)
        self._signature = self._file_signature()
        self._tables_at = time.monotonic()

    @property
    def corpus(self) -> Corpus:
        self.load()
        assert self._corpus is not None  # load() 保证非空
        return self._corpus

    @property
    def index(self) -> LexicalIndex:
        self.load()
        assert self._index is not None
        return self._index

    @property
    def synonyms(self):
        self.load()
        assert self._index is not None
        return self._index.synonyms

    # --------------------------------------------------------
    # 检索
    # --------------------------------------------------------
    def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        """检索与查询相关的口径/表结构/分层文档。

        这是全项目**唯一**的检索入口（图节点、工具、接口都调它），
        因此将来换后端时只需替换 `self.index` 的构造，调用方一行不用改。
        """
        started = time.perf_counter()
        index = self.index
        corpus = self.corpus
        k = top_k if top_k is not None else self.settings.retrieval_top_k

        hits = index.search(query, top_k=k)
        live_status = str(corpus.channels.get("live_tables", ""))
        degraded = not live_status.startswith("ok")

        return RetrievalResult(
            query=query,
            hits=hits,
            expanded_terms=describe_synonym_hits(query, index.synonyms),
            docs_total=len(corpus.docs),
            kinds=iter_kinds(corpus.docs),
            channels=dict(corpus.channels),
            degraded=degraded,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    def describe(self) -> dict[str, Any]:
        """语料与后端自述（供 `/api/retrieve` 的画像与 `/health` 报告）。"""
        corpus = self.corpus
        index = self.index
        return {
            "backend": "lexical-bm25",
            "docs_total": len(corpus.docs),
            "counts_by_kind": corpus.counts(),
            "channels": dict(corpus.channels),
            "loaded_at": corpus.loaded_at,
            "synonyms": {
                "size": index.synonyms.size,
                "source": SYNONYM_FILE,
                "phrases": list(index.synonyms.phrases[:20]),
            },
            "vector_store": None,
            "note": (
                "词法检索（BM25 + 同义词扩展），未引入向量库；"
                "依据 docs/DECISIONS.md ⏳7。"
            ),
        }


def build_retriever(settings: Settings) -> Retriever:
    """构造检索器（不立即装载，首次检索时惰性装载）。"""
    return Retriever(settings)
