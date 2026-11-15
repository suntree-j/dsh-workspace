# -*- coding: utf-8 -*-
"""能力边界的判据测试（纯函数，不依赖 langgraph / 数据库 / 模型）。

为什么这些用例值得留：
1. 越界用例挡住的是"写诗→顺带执行一条无关查询"这条真实发生过的路径；
2. 放行用例挡住的是**过度拦截**——"帮我写一段 SQL 查 GMV"既是"写"又是取数，
   如果被挡，平台会显得连数据问题都不会答。两条方向都必须有用例钉住。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "agent"))

from app.scope import REFUSAL, classify_question_scope, normalize_answer_wording  # noqa: E402


# ---------------- 越界：必须拦 ----------------
def test_poem_about_platform_is_out_of_scope() -> None:
    """真实探针里的原问题：写诗 + 提到"数据平台"。"""
    verdict = classify_question_scope("帮我写一首关于数据平台的诗歌")
    assert verdict.verdict == "out_of_scope"
    assert verdict.reason == "creative"


def test_short_poem_request_is_out_of_scope() -> None:
    verdict = classify_question_scope("来首诗吧")
    assert verdict.verdict == "out_of_scope"


def test_story_and_joke_are_out_of_scope() -> None:
    assert classify_question_scope("讲个笑话").verdict == "out_of_scope"
    assert classify_question_scope("写个故事给我").verdict == "out_of_scope"


def test_translation_is_out_of_scope() -> None:
    assert classify_question_scope("翻译一下这段话").verdict == "out_of_scope"


def test_identity_and_weather_are_out_of_scope() -> None:
    assert classify_question_scope("你是谁").verdict == "out_of_scope"
    assert classify_question_scope("今天天气怎么样").verdict == "out_of_scope"


def test_chitchat_is_out_of_scope() -> None:
    assert classify_question_scope("陪我聊聊天吧").verdict == "out_of_scope"


def test_refusal_mentions_no_numbers() -> None:
    """拒答文案本身不能带数字——否则"越界回答里出现数字"的问题又回来了。"""
    assert not any(ch.isdigit() for ch in REFUSAL)


# ---------------- 放行：绝不能拦 ----------------
def test_metric_question_is_in_scope() -> None:
    assert classify_question_scope("整体 GMV 是多少？").verdict == "in_scope"


def test_reconcile_question_is_in_scope() -> None:
    assert classify_question_scope("实时链路和离线链路算出来的 GMV 对得上吗？").verdict == "in_scope"


def test_write_sql_is_in_scope() -> None:
    """既是"写"又是取数：属于数据问答，不能因为含"写"就被挡。"""
    verdict = classify_question_scope("帮我写一段 SQL 查一下最近的 GMV")
    assert verdict.verdict == "in_scope"


def test_schema_question_is_in_scope() -> None:
    assert classify_question_scope("ads_batch_trade_1m 这张表有哪些字段？").verdict == "in_scope"


def test_trend_and_ranking_are_in_scope() -> None:
    assert classify_question_scope("最近 7 天订单量趋势").verdict == "in_scope"
    assert classify_question_scope("类目 GMV 排名前 5").verdict == "in_scope"


def test_empty_question_defaults_to_allow() -> None:
    """空问题放行：拦在规划阶段报错更好定位，边界判定不做第二件事。"""
    assert classify_question_scope("").verdict == "in_scope"
    assert classify_question_scope("   ").verdict == "in_scope"

# ---------------- 措辞规范化 ----------------
def test_wording_absolute_phrases_are_replaced() -> None:
    """对账结论里的绝对化表述必须被换成可反查的说法。"""
    text, hit = normalize_answer_wording("两侧 GMV 完全一致，逐分钟窗口没有任何差异")
    assert "完全一致" not in text
    assert "没有任何差异" not in text
    assert "逐个相等" in text
    assert set(hit) == {"完全一致", "没有任何差异"}


def test_wording_longer_phrase_wins() -> None:
    """“完全对得上”不能被“完全一致”类的短模式抢先切碎。"""
    text, hit = normalize_answer_wording("GMV 完全对得上")
    assert text == "GMV 对得上"
    assert hit == ["完全对得上"]


def test_wording_keeps_all_numbers_untouched() -> None:
    """只换词，不动数字——数字是证据，措辞不是。"""
    text, _ = normalize_answer_wording("40,358 个窗口完全一致，差异 0.00 元")
    assert "40,358" in text and "0.00" in text


def test_wording_passthrough_when_clean() -> None:
    text, hit = normalize_answer_wording("40,358 个窗口中不一致窗口数为 0")
    assert hit == []
    assert text == "40,358 个窗口中不一致窗口数为 0"