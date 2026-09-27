"""检索层单元测试（Sprint 9，不依赖 LLM、不依赖数据库、不依赖数据服务）。

## 这一组测试真正要锁住的是什么

1. **语料真的装上了**：如果 `metrics.md` 换了格式，指标条数会掉到 0，
   而 Agent 仍然"能回答问题"（只是不再引用口径）—— 这是**静默失败**，
   必须在测试里挡住。
2. **分块没把口径切坏**：GMV 那一块必须同时含 `SUM(amount)` 与
   `ORDER_CREATED`。切坏了模型会照着残缺的文本写 SQL。
3. **同义词真的生效**：一个**不含 `GMV` 字样**的问法必须命中 GMV 口径。
   这是 Sprint 9 的核心可验收项，也是词法检索唯一需要额外机制的地方。
4. **负例**：不相干的问题**不能**把口径文档排到第一。
   只测正例的话，"什么都映射"的同义词表也能全绿 —— 那种表比没有更糟。

依据：AGENTS.md 第 8.2 节（单元测试不得依赖外部服务）。
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings, load_settings
from app.corpus import Doc, load_corpus, parse_markdown_table
from app.lexical import BM25_K1, TITLE_BONUS, LexicalIndex, expand_query, load_synonyms, tokenize
from app.retrieval import TABLE_TTL_SECONDS, Retriever, build_retriever

pytestmark = pytest.mark.unit

SYNONYM_FILE = "services/agent/knowledge/synonyms.json"


def _settings(**overrides: object) -> Settings:
    base = load_settings()
    return Settings(**{**base.__dict__, **overrides})


def _retriever(**overrides: object) -> Retriever:
    """离线检索器：关掉"经接口取表结构"这一路，保证单元测试零外部依赖。"""
    retriever = build_retriever(_settings(**overrides))
    retriever.include_live_tables = False
    return retriever


# ============================================================
# 1. markdown 表格解析
# ============================================================
def test_parse_markdown_table_reports_real_line_numbers() -> None:
    text = "\n".join(
        [
            "# 标题",
            "",
            "| 指标 | 字段名 |",
            "| --- | --- |",
            "| GMV | `gmv` |",
        ]
    )
    rows = parse_markdown_table(text)
    assert len(rows) == 1, "表头与分隔行都不应算作数据行"
    line_no, cells = rows[0]
    assert line_no == 5, "行号必须是原文中的真实行号（引用要能点回去）"
    assert cells == ["GMV", "`gmv`"]


# ============================================================
# 2. 语料装载
# ============================================================
def test_corpus_loads_metrics_tables_and_layering() -> None:
    corpus = load_corpus(_settings(), include_live_tables=False)

    assert len(corpus.docs) >= 40, f"语料条数异常偏少：{len(corpus.docs)}"
    kinds = corpus.counts()
    assert kinds.get("metric", 0) >= 20, "指标口径条目应至少 20 条"
    assert kinds.get("layering", 0) >= 5, "分层说明应有多个段落"
    assert corpus.channels["sql/metadata/metrics.md"].startswith("ok")
    assert corpus.channels["live_tables"] == "已跳过"


def test_gmv_doc_keeps_calculation_and_event_type() -> None:
    """分块不能把口径切坏：GMV 块必须含计算方式与事件类型。"""
    corpus = load_corpus(_settings(), include_live_tables=False)
    gmv = [doc for doc in corpus.docs if doc.doc_id == "metric:gmv"]
    assert len(gmv) == 1, "同名指标必须合并成一条（否则会与类目口径争抢第一名）"
    text = gmv[0].text
    assert "SUM(amount)" in text
    assert "ORDER_CREATED" in text
    assert gmv[0].source == "sql/metadata/metrics.md"
    assert gmv[0].line > 0


def test_ratio_metric_documents_are_present() -> None:
    corpus = load_corpus(_settings(), include_live_tables=False)
    ids = {doc.doc_id for doc in corpus.docs}
    for field in (
        "metric:payment_success_rate",
        "metric:refund_rate",
        "metric:avg_order_amount",
        "metric:order_user_cnt",
    ):
        assert field in ids, f"缺少口径条目：{field}"


def test_load_corpus_reports_missing_file_instead_of_failing() -> None:
    """缺文件必须被如实报告，而不是静默少一路语料。"""
    corpus = load_corpus(_settings(repo_root=_settings().repo_root / "不存在的目录"), False)
    assert corpus.docs == []
    assert "缺失" in json.dumps(corpus.channels, ensure_ascii=False)


# ============================================================
# 3. 分词
# ============================================================
def test_tokenize_chinese_uses_bigrams() -> None:
    tokens = tokenize("复购率")
    assert "复购" in tokens and "购率" in tokens
    assert "复购率" in tokens, "整串也要保留，精确匹配时才拿得到最高权重"


def test_tokenize_drops_isolated_cjk_chars() -> None:
    """孤立中文单字（算、的、是）几乎出现在每篇文档里，是纯噪声。"""
    tokens = tokenize("算")
    assert tokens == []
    assert "怎么" in tokenize("怎么算")


def test_tokenize_keeps_underscored_identifier_whole() -> None:
    tokens = tokenize("ads_batch_trade_1d")
    assert "ads_batch_trade_1d" in tokens
    assert "adsbatchtrade1d" in tokens, "用户可能不带下划线地写表名"


# ============================================================
# 4. 同义词表
# ============================================================
def test_synonym_table_loads_and_is_not_empty() -> None:
    table = load_synonyms(_settings().repo_root / SYNONYM_FILE)
    assert table.size >= 20
    assert "卖了多少钱" in table.mapping
    assert table.mapping["卖了多少钱"], "映射的术语不能为空"


def test_synonym_table_rejects_malformed_entry(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"synonyms": [{"phrase": "有词无术语", "terms": []}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_synonyms(bad)


def test_expand_query_maps_colloquial_phrase_to_gmv() -> None:
    table = load_synonyms(_settings().repo_root / SYNONYM_FILE)
    weights, matches = expand_query("最近一周卖了多少钱", table)
    assert "gmv" in weights, "口语问法必须扩展出 gmv"
    phrases = {item["phrase"] for item in matches}
    assert "卖了多少钱" in phrases
    # 每条生效的同义词都要带理由（无理由的映射会让检索退化成"总能命中点什么"）
    for item in matches:
        assert item["reason"], f"同义词 {item['phrase']} 缺少理由"


def test_expand_query_prefers_longer_phrase_weight() -> None:
    table = load_synonyms(_settings().repo_root / SYNONYM_FILE)
    long_weights, _ = expand_query("卖了多少钱", table)
    short_weights, _ = expand_query("口径", table)
    long_gmv = long_weights["gmv"]
    short_def = short_weights["metric_definitions"]
    assert long_gmv > short_def, "越具体的短语应给越高的扩展权重"


def test_expand_query_drops_amount_noise_when_compound_present() -> None:
    """`payment_amount` 里的 `amount` 是噪声：它出现在几乎每一行业务指标里。"""
    table = load_synonyms(_settings().repo_root / SYNONYM_FILE)
    weights, _ = expand_query("payment_amount", table)
    assert "payment_amount" in weights
    assert "amount" not in weights


# ============================================================
# 5. BM25 检索（用小型自造语料，行为可精确断言）
# ============================================================
def _tiny_index() -> LexicalIndex:
    synonyms = load_synonyms(_settings().repo_root / SYNONYM_FILE)
    docs = [
        Doc("metric:gmv", "GMV（gmv）", "指标 GMV 口径 SUM(amount) ORDER_CREATED", "m.md", 33, "metric"),
        Doc("metric:uv", "UV（uv）", "指标 UV 窗口内去重用户数 COUNT(DISTINCT user_id)", "m.md", 90, "metric"),
        Doc("table:x", "表 ecommerce.ads_realtime_trade_1m", "表结构 字段 gmv order_cnt", "api", 0, "table"),
    ]
    return LexicalIndex.build(docs, synonyms)


def test_bm25_ranks_title_match_first() -> None:
    hits = _tiny_index().search("gmv", top_k=3)
    assert hits, "必须有命中"
    assert hits[0].doc.doc_id == "metric:gmv"
    assert hits[0].relevance == pytest.approx(1.0), "最高分命中的 relevance 归一为 1.0"


def test_bm25_returns_empty_for_unrelated_query() -> None:
    """负例：完全不相干的查询不能硬凑出命中。

    没有这条断言，"同义词表把所有词都映射一遍"也能让正例全绿。
    """
    assert _tiny_index().search("今天天气怎么样适合钓鱼吗", top_k=3) == []


def test_bm25_zero_score_documents_are_dropped() -> None:
    hits = _tiny_index().search("uv", top_k=5)
    ids = [hit.doc.doc_id for hit in hits]
    assert ids[0] == "metric:uv"
    # 与 uv 无关的表结构文档不应出现在结果里
    assert "table:x" not in ids or any(
        hit.doc.doc_id == "table:x" and hit.score > 0 for hit in hits
    )


def test_title_bonus_constant_is_documented() -> None:
    """标题加成是修正"提到某词压过定义该词"的关键，改动必须是有意的。"""
    assert TITLE_BONUS > 0
    assert BM25_K1 > 0


# ============================================================
# 6. 端到端检索行为（真实语料）
# ============================================================
def test_retrieve_hits_gmv_metric_for_direct_question() -> None:
    result = _retriever().retrieve("GMV 怎么算")
    assert result.docs_total >= 40
    assert result.hits, "必须有命中"
    assert result.hits[0].doc.doc_id == "metric:gmv"
    assert result.hits[0].doc.citation().startswith("sql/metadata/metrics.md:")


def test_retrieve_synonym_makes_colloquial_question_hit_gmv() -> None:
    """Sprint 9 的核心验收项：**不含 GMV 字样**的问法必须命中 GMV 口径。"""
    question = "最近一周卖了多少钱"
    assert "gmv" not in question.lower(), "前提：问题里不能出现 gmv，否则测不到同义词"

    result = _retriever().retrieve(question)

    assert result.expanded_terms, "同义词必须生效"
    pairs = {item["phrase"]: item["terms"] for item in result.expanded_terms}
    assert "卖了多少钱" in pairs
    assert "gmv" in pairs["卖了多少钱"]

    top_ids = [hit.doc.doc_id for hit in result.hits]
    assert "metric:gmv" in top_ids, f"询问成交金额必须命中 GMV 口径，实际命中：{top_ids}"


def test_retrieve_reports_source_file_and_line_for_every_hit() -> None:
    result = _retriever().retrieve("支付成功率怎么算")
    assert result.hits
    for hit in result.hits:
        assert hit.doc.source, "每条命中都必须能说清来源文件"
        assert hit.doc.citation().count(":") >= 1


def test_retrieve_is_degraded_but_usable_without_live_tables() -> None:
    """表结构那一路取不到时：检索仍可用，但必须**如实标注**降级。"""
    result = _retriever().retrieve("gmv")
    assert result.degraded is True
    assert result.channels["live_tables"] == "已跳过"
    assert result.hits, "少一路语料不应让检索整体失效"


def test_retrieve_top_k_is_respected() -> None:
    retriever = _retriever(retrieval_top_k=2)
    assert len(retriever.retrieve("gmv 口径").hits) <= 2
    assert len(retriever.retrieve("gmv 口径", top_k=1).hits) == 1


def test_retrieve_context_carries_citations_and_synonym_note() -> None:
    """给 LLM 的上下文必须带来源标注 —— 否则回答里的引用只能靠模型编。"""
    result = _retriever().retrieve("最近一周卖了多少钱")
    context = result.as_context()
    assert "[来源 sql/metadata/metrics.md:" in context
    assert "同义词扩展生效" in context


def test_retrieve_context_says_so_when_nothing_matched() -> None:
    result = _retriever().retrieve("今天天气怎么样适合钓鱼吗")
    assert result.hits == []
    context = result.as_context()
    assert "没有命中" in context
    assert "不要" in context, "必须在上下文里明令不要凭常识给公式"


def test_retriever_cache_is_invalidated_by_table_ttl() -> None:
    """TTL 到期必须**重新装载**（否则新增的表永远不可见，而且是静默的）。

    断言的是"发生了重装"，不是"对象换了"：重装会新建 Corpus，
    但真正要保证的是**接口被重新访问过**，所以检查 `loaded_at` 与 `_tables_at`。
    """
    retriever = _retriever()
    retriever.load()
    assert retriever._needs_reload() is False, "刚装载完不该立刻重装"

    first_tables_at = retriever._tables_at

    # 模拟"表结构 TTL 已过期"
    retriever._tables_at -= TABLE_TTL_SECONDS + 1
    assert retriever._needs_reload() is True

    retriever.load()
    assert retriever._tables_at > first_tables_at, "TTL 到期后必须重新走一次表结构装载"
    assert retriever.corpus.docs, "重装后语料仍非空"
    # 语料文件没变，内容应当仍然正确
    assert retriever.corpus.counts().get("metric", 0) >= 20


def test_describe_reports_no_vector_store() -> None:
    profile = _retriever().describe()
    assert profile["backend"] == "lexical-bm25"
    assert profile["vector_store"] is None, "本 Sprint 明确不引入向量库"
