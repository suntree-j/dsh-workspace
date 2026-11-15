# -*- coding: utf-8 -*-
"""能力边界：判断一个问题是否属于"数据问答"范围。

为什么需要这个模块
------------------
智能体是**数据分析平台的自然语言入口**，不是通用聊天助手。上线探针暴露过两件事：

1. 问"帮我写一首关于数据平台的诗歌"，它照写了，而且为了给诗配"真实锚点"，
   还额外执行了一条流量域对账查询 —— 一次与问题无关的取数；
2. 这次回答带着真实批次号与窗口数，读起来像"平台的能力"，其实是越界。

越界本身不算数据错误，但它破坏了一条更重要的性质：**"回答里出现的每个数字都来自
为这个问题执行过的查询"**。写诗不需要取数，一旦取数，数字就变成装饰品。

设计取向：宁可多答一条，也不错挡一条
------------------------------------
本模块**只挡明确的创作/闲聊请求**（写诗、写小说、讲笑话、翻译、闲聊、问身份、问天气），
且这些模式与"取数"没有交集。凡是可能涉及数据、口径、表、指标、链路的问法一律放行，
包括"帮我写一段 SQL 查 GMV"这类既是"写"又是"取数"的问法 —— 它属于数据问答。

判据可复核：`classify_question_scope()` 是纯函数，输入问题、输出判定，
不读配置、不连数据库、不调模型；对应的单测在 `tests/test_agent_scope.py`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# 明确的创作/闲聊模式。刻意与"取数"不交叉：
# 不含"代码/脚本/SQL"——那三种常常是合法的取数请求。
_NON_DATA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("creative", re.compile(r"(写|作|来|吟|编|讲|说)[^，。！？?!]{0,10}(诗|歌词|词牌|小说|故事|作文|散文|段子|笑话|对联|情书)")),
    ("translate", re.compile(r"(翻译|润色一下|帮我润色)")),
    ("chitchat", re.compile(r"(聊聊天|聊会儿|陪我聊|闲聊)")),
    ("identity", re.compile(r"(你是|你叫)(谁|什么名字)|你是什么(模型|东西)")),
    ("weather", re.compile(r"天气(怎么样|如何|预报)")),
)

REFUSAL = (
    "这个问题不在我能回答的范围内。\n\n"
    "我是这个数据分析平台的自然语言入口，只回答与平台数据有关的问题，例如：\n"
    "- 某个指标是多少（GMV、订单量、支付金额、退款额、PV/UV、客单价……）\n"
    "- 某段时间的趋势、排名、占比、漏斗转化\n"
    "- 实时链路与离线链路的结果是否一致（批流对账）\n"
    "- 某个指标的口径定义、某张表里有哪些字段\n\n"
    "创作、翻译、闲聊类请求我不会处理 —— 这类请求不需要取数，"
    "而我的每一条回答都要求「给出的每个数字都来自为这个问题真实执行过的查询」。"
)


@dataclass(frozen=True)
class ScopeVerdict:
    """一次能力边界判定的结果。"""

    verdict: str  # "in_scope" / "out_of_scope"
    reason: str  # 命中的模式名，或 "default_allow"
    refusal: str = ""  # 越界时才有的固定拒答文案

    @property
    def allowed(self) -> bool:
        return self.verdict == "in_scope"


def classify_question_scope(question: str) -> ScopeVerdict:
    """判断问题是否属于数据问答范围。

    规则只有一条：命中"明确的创作/闲聊模式"即越界；其余一律放行（默认放行）。
    这样设计是为了让误挡的代价为零——即使某条创作请求被放行，
    它最坏也只是被规划成一次无意义的查询；而误挡一条真实的取数问题，
    用户会直接认为"这个平台不好用"。
    """

    text = (question or "").strip()
    if not text:
        return ScopeVerdict(verdict="in_scope", reason="default_allow")

    for name, pattern in _NON_DATA_PATTERNS:
        if pattern.search(text):
            return ScopeVerdict(verdict="out_of_scope", reason=name, refusal=REFUSAL)

    return ScopeVerdict(verdict="in_scope", reason="default_allow")


# 绝对化表述 → 可反查表述。**按长度从长到短匹配**，否则"完全对得上"会被
# "完全一致"这类短模式抢先切碎。
_WORDING_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("完全对得上", "对得上"),
    ("完全没有差异", "不一致为 0"),
    ("没有任何差异", "不一致为 0"),
    ("完全没有区别", "逐个相等"),
    ("完全一致", "逐个相等"),
    ("毫无差异", "不一致为 0"),
    ("零差异", "差异为 0"),
    ("一模一样", "逐个相等"),
)


def normalize_answer_wording(answer: str) -> tuple[str, list[str]]:
    """把对账结论里的绝对化表述换成可被数字反查的说法。

    为什么必须做：一次比对能说的只有"我比了 N 个窗口，其中 0 个不等"。
    "完全一致"听起来像对两套系统的绝对保证，而绝对保证不是一次比对能给出的事实。
    这个函数只做**词面替换**，不增删任何数字，也不改变结论方向。

    返回 (替换后的文本, 命中的原词列表)；命中列表会写进 graph trace，
    使"这句话被改写过"本身也是可复核的。
    """

    text = answer or ""
    hit: list[str] = []
    for raw, safe in _WORDING_REPLACEMENTS:
        if raw in text:
            hit.append(raw)
            text = text.replace(raw, safe)
    return text, hit
