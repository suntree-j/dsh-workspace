"""词法检索后端：BM25 + 同义词扩展 + 中文分词（Sprint 9）。

## 为什么是 BM25 而不是向量检索（结论与依据）

见 [`docs/DECISIONS.md`](../../../docs/DECISIONS.md) ⏳7 与
[`docs/sprint/SPRINT_9.md`](../../../docs/sprint/SPRINT_9.md) 第 2 节，要点：

1. DeepSeek 官方 API **没有 embeddings 端点**（已查证），走向量检索必须
   再引入一个外部供应商与 Key；
2. 本机可用内存约 4.3 GB（实时链路常驻约 13.7 GB），**放不下**本地 embedding 模型；
3. `Milvus` / `Elasticsearch` 属 `AGENTS.md` §2.3 **未批准技术栈**。

而本项目的语料只有几十条，提问又高度依赖关键词（`GMV`、`UV`、`ads_batch_trade_1d`…），
BM25 既够用又**命中理由可解释** —— 这一点对本项目尤其重要，
因为回答本来就要给出"依据的是哪条口径"。

## 中文没有空格：分词怎么做

不引入 `jieba`（新增依赖），采用中文 IR 的常规做法：

- ASCII（含数字、下划线标识符）按词边界切分，并保留下划线整体
  （`ads_realtime_trade_1m` 是一个词，比切成 4 段更有区分度）；
- **CJK 连续串切 bigram**：`复购率` → `复购` / `购率`。
  bigram 能在不依赖词典的前提下覆盖绝大多数中文查询词，代价是索引略大。

## 同义词表：把"词法检索的代价"显式补上

BM25 是词项匹配，用户问"卖了多少钱"而语料里写的是 `GMV` 时必然漏召回。
对策是 [`knowledge/synonyms.json`](../../knowledge/synonyms.json)：
一份**可审查、可测试**的映射配置（每条都带理由），而不是散落在代码里的 if-else。

短语命中的术语会带**长度相关权重**：`卖了多少钱`（5 字）比 `口径`（2 字）
更能确定用户在问什么，因此前者权重更高。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .corpus import Doc

__all__ = ["ScoredDoc", "LexicalIndex", "SynonymTable", "tokenize", "load_synonyms"]

# BM25 的两个标准参数：k1 控制词频饱和，b 控制长度归一化。
# 取常用默认值（1.2 / 0.75）而不是调参：语料只有几十条，
# 在这么小的集合上调参等于对着噪声拟合。
BM25_K1 = 1.2
BM25_B = 0.75

# ASCII 词：字母开头，允许数字与下划线（这样 `ads_realtime_trade_1m` 是一个词）
_ASCII_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

# 标题里的词项额外加分。
#
# !! 为什么必须有这一项（实测踩坑）!!
#   只算正文 BM25 时，问「最近一周卖了多少钱」（同义词 → gmv / payment_amount）
#   排第一的是 metrics.md 里那句**列举了所有可加指标**的约定行
#   （"可加指标 gmv / order_cnt / payment_amount / …"）——
#   它同时命中了两个查询词项，长度惩罚也不足以压住它。
#   而真正定义 GMV 的那一行只命中一个词项，于是被排到了第二。
#
#   这正是"词袋模型没有字段概念"的典型症状：**提到**某个词的行
#   压过了**定义**那个词的行。而用户要的恰恰是定义。
#   加一个标题命中加成即可修正，且这个理由是可以在验收里复现与被验证的。
TITLE_BONUS = 1.0


def tokenize(text: str, bigram: bool = True, drop_single_cjk: bool = True) -> list[str]:
    """把一段文本切成检索词。

    参数：
        bigram=True（默认）用于**索引与查询**；
        bigram=False 只切 ASCII，用于"用户到底提到了哪个术语"这类判断。
        drop_single_cjk=True 丢弃**孤立的中文单字**。

    !! 为什么要丢弃孤立中文单字（实测踩坑）!!
        `怎么算` 会切出 bigram `怎么` / `么算`，但 `算` 也必须被切出来，
        否则 `枚举` 这类 3 字词会漏掉尾巴。问题在于中文字里很多单字是
        **虚词或通用动词**（算、的、了、是、有），它们几乎出现在每一篇文档里，
        idf 很低却数量极多，于是"命中理由"被噪声填满 ——
        表现为检索结果里出现一堆 matched_terms，看起来命中了，实则无关。
        语料里真正有区分度的中文实体（`客单价`、`复购`、`漏斗`）
        都由 bigram 覆盖，因此丢弃孤立单字几乎不损失召回。
    """
    if not text:
        return []

    tokens: list[str] = []
    lowered = text.lower()

    for match in _ASCII_RE.finditer(lowered):
        token = match.group(0)
        tokens.append(token)
        # 下划线标识符额外产出"去掉下划线"的连写形式：
        # 用户可能写 `ads realtime trade 1m`，而语料里是 `ads_realtime_trade_1m`。
        if "_" in token:
            tokens.append(token.replace("_", ""))

    if bigram:
        for match in _CJK_RUN_RE.finditer(text):
            run = match.group(0)
            if len(run) == 1:
                if not drop_single_cjk:
                    tokens.append(run)
                continue
            # 整串也保留：查询恰好等于语料中的整词时能拿到最高权重
            tokens.append(run)
            for index in range(len(run) - 1):
                tokens.append(run[index : index + 2])

    return tokens


# ------------------------------------------------------------
# 同义词表
# ------------------------------------------------------------
@dataclass(frozen=True)
class SynonymTable:
    """同义词映射（从 JSON 装载，非法项直接拒绝而不是静默忽略）。"""

    phrases: tuple[str, ...]
    mapping: dict[str, tuple[str, ...]]
    reasons: dict[str, str]
    stopwords: frozenset[str]
    term_weight: dict[str, float]
    source: str = ""

    @property
    def size(self) -> int:
        return len(self.phrases)


def load_synonyms(path: Path) -> SynonymTable:
    """装载同义词表。

    !! 为什么非法项要报错而不是跳过 !!
        同义词表是检索召回率的关键。若某个短语缺 `terms`，
        静默跳过会让"同义词生效了"这件事在验收时无法判断
        （表现为：测试通过、真实提问却召不回）。
        宁可启动即失败，也不要一份看起来加载成功的坏配置。
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    mapping: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    for item in raw.get("synonyms", []):
        phrase = str(item.get("phrase", "")).strip().lower()
        terms = [str(term).strip().lower() for term in item.get("terms", []) if str(term).strip()]
        if not phrase or not terms:
            raise ValueError(f"同义词表存在非法项：{item!r}（phrase 与 terms 都必填）")
        bucket = mapping.setdefault(phrase, [])
        for term in terms:
            if term not in bucket:
                bucket.append(term)
        # 同一短语出现多次时保留第一条理由（后续的是补充召回，不是新理由）
        reasons.setdefault(phrase, str(item.get("reason", "")).strip())

    # 长短语优先：先匹配"卖了多少钱"，再匹配"卖了"
    phrases = tuple(sorted(mapping, key=len, reverse=True))
    return SynonymTable(
        phrases=phrases,
        mapping={key: tuple(value) for key, value in mapping.items()},
        reasons=reasons,
        stopwords=frozenset(str(word).lower() for word in raw.get("stopwords", [])),
        term_weight={
            str(key).lower(): float(value) for key, value in (raw.get("term_weight") or {}).items()
        },
        source=str(path),
    )


# ------------------------------------------------------------
# 打分结果
# ------------------------------------------------------------
@dataclass
class ScoredDoc:
    """一条命中：文档 + 分数 + **为什么命中**。"""

    doc: Doc
    score: float
    matched_terms: list[str] = field(default_factory=list)
    relevance: float = 0.0  # 归一化到 [0,1]（最高分命中为 1.0）

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc.doc_id,
            "title": self.doc.title,
            "kind": self.doc.kind,
            "source": self.doc.source,
            "line": self.doc.line,
            "citation": self.doc.citation(),
            "score": round(self.score, 4),
            "relevance": round(self.relevance, 4),
            "matched_terms": self.matched_terms,
            "text": self.doc.text,
        }


def expand_query(
    query: str, synonyms: SynonymTable
) -> tuple[dict[str, float], list[dict[str, str]]]:
    """把查询词扩展成"词项 → 权重"，并记录每条同义词是怎么生效的。

    权重设计（刻意简单可解释）：
        原始词项            权重 1.0
        同义词术语          权重 = min(0.5 + 0.1 × 短语字数, 1.0)
    理由：短语越长，用户意图越明确（"卖了多少钱" 比 "金额" 更具体），
    因此给更接近原始词项的权重；短语越短越含糊，权重越低。

    返回：
        terms    词项 → 权重
        matches  [{"phrase": ..., "terms": [...], "reason": ...}]（用于可解释性）
    """
    lowered = query.lower()
    tokens = [token for token in tokenize(query) if token not in synonyms.stopwords]

    weights: dict[str, float] = {}
    for token in tokens:
        weights[token] = max(weights.get(token, 0.0), 1.0)

    matches: list[dict[str, str]] = []
    for phrase in synonyms.phrases:
        if phrase not in lowered:
            continue
        terms = synonyms.mapping[phrase]
        weight = min(0.5 + 0.1 * len(phrase), 1.0)
        for term in terms:
            weights[term] = max(weights.get(term, 0.0), weight)
        matches.append(
            {
                "phrase": phrase,
                "terms": list(terms),
                "weight": f"{weight:.2f}",
                "reason": synonyms.reasons.get(phrase, ""),
            }
        )

    # 词项去噪：只保留有区分度的词项。
    #
    # !! 为什么必须去噪（实测踩坑）!!
    #   语料里的英文指标名（`payment_amount`）自身含子串 `amount`，
    #   于是 ASCII 切分会同时产出 `payment_amount` 与 `amount`；
    #   而 `amount` 出现在**每一行业务指标**里，idf 低但覆盖广，
    #   会把"支付成功率"这类无关文档也拉进结果。
    #   判据：**给长词项当子串的短词项直接丢弃**（长词项已经表达了同一信息）。
    #   注意用 `in` 而不是"端点匹配"：`amount` 在 `payment_amount` 里不是端点，
    #   而它恰恰是噪声来源。
    singles = {token for token in weights if "_" not in token}
    compounds = [token for token in weights if "_" in token]
    for short in singles:
        if any(short in long for long in compounds):
            weights.pop(short, None)

    return weights, matches


# ------------------------------------------------------------
# BM25 索引
# ------------------------------------------------------------
@dataclass
class LexicalIndex:
    """BM25 索引（纯内存、纯函数，可离线单元测试）。"""

    docs: list[Doc]
    synonyms: SynonymTable
    doc_tokens: list[list[str]] = field(default_factory=list)
    title_tokens: list[set[str]] = field(default_factory=list)
    doc_freq: dict[str, int] = field(default_factory=dict)
    doc_len: list[int] = field(default_factory=list)
    avg_len: float = 0.0

    @classmethod
    def build(cls, docs: list[Doc], synonyms: SynonymTable) -> LexicalIndex:
        index = cls(docs=list(docs), synonyms=synonyms)
        index.doc_tokens = [tokenize(index._field(doc)) for doc in index.docs]
        index.title_tokens = [
            set(tokenize(f"{doc.title} {doc.doc_id}", bigram=False)) for doc in index.docs
        ]
        index.doc_len = [len(tokens) for tokens in index.doc_tokens]
        index.avg_len = (sum(index.doc_len) / len(index.doc_len)) if index.doc_len else 0.0

        freq: dict[str, int] = {}
        for tokens in index.doc_tokens:
            for token in set(tokens):
                freq[token] = freq.get(token, 0) + 1
        index.doc_freq = freq
        return index

    @staticmethod
    def _field(doc: Doc) -> str:
        """参与检索的字段。

        `title` 重复一次（标题里的指标名比正文更能代表这条文档），
        这是 BM25 领域字段加权的朴素做法，不需要额外机制。
        """
        return f"{doc.title} {doc.title} {doc.text} {doc.kind}"

    def _idf(self, token: str) -> float:
        total = len(self.docs)
        freq = self.doc_freq.get(token, 0)
        # 标准 BM25 的 idf，含 +0.5 平滑；freq=0 时仍返回正的极小值，
        # 这样查询里的生僻词不会把整条查询的分数打成 0。
        if total == 0:
            return 0.0
        return math.log(1.0 + (total - freq + 0.5) / (freq + 0.5))

    def search(self, query: str, top_k: int = 5) -> list[ScoredDoc]:
        """检索并返回按分数降序的命中（分数为 0 的不返回）。"""
        weights, _ = expand_query(query, self.synonyms)
        if not weights or not self.docs:
            return []

        scored: list[ScoredDoc] = []
        for position, tokens in enumerate(self.doc_tokens):
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1

            length = self.doc_len[position]
            norm = 1.0 - BM25_B + BM25_B * (length / self.avg_len) if self.avg_len else 1.0
            titles = self.title_tokens[position]

            total = 0.0
            matched: list[str] = []
            for term, weight in weights.items():
                freq = counts.get(term, 0)
                if freq == 0:
                    # 词项没出现在正文，但可能就在标题里（例如表名就是标题的一部分）
                    if term in titles:
                        matched.append(term)
                        total += weight * self._idf(term) * TITLE_BONUS
                    continue
                matched.append(term)
                total += weight * self._idf(term) * (freq * (BM25_K1 + 1)) / (freq + BM25_K1 * norm)
                if term in titles:
                    total += weight * self._idf(term) * TITLE_BONUS

            if total <= 0:
                continue
            scored.append(
                ScoredDoc(
                    doc=self.docs[position],
                    score=total,
                    matched_terms=sorted(matched),
                )
            )

        scored.sort(key=lambda item: (-item.score, item.doc.doc_id))
        top = scored[: max(1, top_k)]
        if top:
            best = top[0].score
            for item in top:
                item.relevance = item.score / best if best > 0 else 0.0
        return top


def describe_synonym_hits(query: str, synonyms: SynonymTable) -> list[dict[str, str]]:
    """只返回"哪些同义词生效了"，供接口与验收脚本断言。"""
    _, matches = expand_query(query, synonyms)
    return matches


def iter_kinds(docs: Iterable[Doc]) -> list[str]:
    """文档类别清单（去重、保序），供 /api/retrieve 报告语料构成。"""
    seen: list[str] = []
    for doc in docs:
        if doc.kind not in seen:
            seen.append(doc.kind)
    return seen
