"""SQL 安全守卫（服务层唯一允许构造 SQL 的入口）。

对应 AGENTS.md 第 10.2 / 10.3 节：
    只允许 SELECT；拒绝多语句、注释注入、DDL/DML；
    必须带 LIMIT；必须能说明数据来源。

设计取舍：
    这里做的是**白名单式**校验（表名必须在白名单里、语句必须是 SELECT），
    而不是黑名单式"禁止某些关键字" —— 黑名单永远列不全。
    参数值一律走数据库驱动的占位符绑定，绝不字符串拼接。

本模块是**纯函数**，不依赖数据库，因此可以完整跑单元测试
（tests/test_sql_guard.py）。
"""

from __future__ import annotations

import re

__all__ = ["SqlRejected", "validate_select", "ALLOWED_TABLES"]


class SqlRejected(ValueError):
    """SQL 未通过安全校验。code 用于接口返回，便于前端与测试断言。"""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


# ------------------------------------------------------------
# 白名单：只允许查询这些表（全部在 ecommerce 库内）
# 分层前缀即权限边界 —— 新增表必须显式加进来，避免"顺手查了别的库"
# ------------------------------------------------------------
BUSINESS_TABLES = {
    # DWD 明细层
    "dwd_trade_order_detail",
    "dwd_trade_payment_detail",
    "dwd_trade_refund_detail",
    "dwd_traffic_behavior_detail",
    # DWS 汇总层
    "dws_traffic_overview_1m",
    # ADS 应用层
    "ads_realtime_trade_1m",
    "ads_realtime_traffic_1m",
    "ads_realtime_category_1m",
    # 维表
    "dim_product",
    "dim_user",
}

# 元数据查询（只在 /meta/tables 接口里由本服务自己拼接，不接受用户输入）
METADATA_TABLES = {"tables", "columns"}

ALLOWED_TABLES = BUSINESS_TABLES | METADATA_TABLES

# 明确禁止出现在语句中的关键字（即使以白名单表为目标也不允许）
_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "set", "use", "call", "load", "export", "import",
    "replace", "rename", "backup", "restore", "kill", "shutdown",
    "into outfile", "into dumpfile", "load_file", "benchmark", "sleep",
)

# 注释与多语句：任何情况下都不允许（注释注入是绕过校验的常见手法）
_FORBIDDEN_PATTERNS = (
    (re.compile(r";"), "MULTI_STATEMENT", "不允许出现分号（多语句）"),
    (re.compile(r"--"), "COMMENT", "不允许 SQL 注释（--）"),
    (re.compile(r"/\*"), "COMMENT", "不允许 SQL 注释（/* */）"),
    (re.compile(r"\*/"), "COMMENT", "不允许 SQL 注释（/* */）"),
    (re.compile(r"#"), "COMMENT", "不允许 SQL 注释（#）"),
    (re.compile(r"\bunion\s+all\s+select\b.*\bfrom\s+information_schema\b", re.I | re.S),
     "METADATA_PROBE", "不允许通过 UNION 探测元数据"),
)

# 提取语句中出现的目标表：FROM / JOIN 后面的标识符。
# 需要支持 `库`.`表`、库.表、`表`、[表] 等写法 —— 少识别一种写法就等于少一道防线，
# 因此这里把"标识符"拆成可重复的"点分片段"。
_IDENT = r"(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][\w$]*)"
_TABLE_RE = re.compile(rf"\b(?:from|join)\s+({_IDENT}(?:\s*\.\s*{_IDENT})*)", re.I)

# 提取 LIMIT 子句。
# MySQL/Doris 有三种写法，必须分别处理（语义不同，混淆会把 offset 当 count）：
#   LIMIT count              —— 最常见
#   LIMIT count OFFSET skip  —— 标准 SQL
#   LIMIT skip, count        —— MySQL 方言
_LIMIT_COUNT_RE = re.compile(r"\blimit\s+(\d+)\s*$", re.I)
_LIMIT_OFFSET_RE = re.compile(r"\blimit\s+(\d+)\s+offset\s+(\d+)\s*$", re.I)
_LIMIT_COMMA_RE = re.compile(r"\blimit\s+(\d+)\s*,\s*(\d+)\s*$", re.I)


def _normalize_table(raw: str) -> str:
    """把 `ecommerce`.`ads_xxx` / ecommerce.ads_xxx / [ads_xxx] 统一成表名。"""
    last = re.split(r"\s*\.\s*", raw.strip())[-1]
    return last.strip().strip("`\"[]")


def validate_select(sql: str, max_limit: int = 1000) -> str:
    """校验并规范化一条 SELECT 语句。

    返回：补上/收敛 LIMIT 之后的语句（仍应使用参数绑定执行）。
    抛出：SqlRejected（带 code / message / detail）。
    """
    if not sql or not sql.strip():
        raise SqlRejected("EMPTY_SQL", "SQL 为空")

    text = sql.strip()
    lowered = text.lower()

    # 1) 必须以 SELECT 开头（不接受 WITH / SHOW / EXPLAIN 等）
    if not lowered.startswith("select"):
        raise SqlRejected(
            "NOT_SELECT",
            "只允许 SELECT 查询",
            f"实际语句以 {text.split()[0]!r} 开头",
        )

    # 2) 注释与多语句
    for pattern, code, message in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            raise SqlRejected(code, message, f"命中：{pattern.pattern}")

    # 3) 危险关键字（带词边界，避免误伤列名如 create_time）
    for keyword in _FORBIDDEN_KEYWORDS:
        pattern = r"\b" + keyword.replace(" ", r"\s+") + r"\b"
        if re.search(pattern, lowered):
            raise SqlRejected("FORBIDDEN_KEYWORD", f"语句中包含禁止的关键字：{keyword}")

    # 4) 表白名单
    tables = {_normalize_table(match.group(1)) for match in _TABLE_RE.finditer(text)}
    if not tables:
        raise SqlRejected("NO_TABLE", "语句中没有可识别的表名")
    illegal = sorted(t for t in tables if t not in ALLOWED_TABLES)
    if illegal:
        raise SqlRejected(
            "TABLE_NOT_ALLOWED",
            "查询了未被授权的表",
            f"未授权：{', '.join(illegal)}；允许的表见 sqlguard.ALLOWED_TABLES",
        )

    # 5) 强制 LIMIT：没有就补，太大就收
    if max_limit <= 0:
        raise SqlRejected("BAD_LIMIT", "max_limit 配置非法")

    # 5.1 LIMIT count OFFSET skip
    match = _LIMIT_OFFSET_RE.search(text)
    if match is not None:
        count = min(int(match.group(1)), max_limit)
        return f"{text[:match.start()]} LIMIT {count} OFFSET {int(match.group(2))}"

    # 5.2 LIMIT skip, count（MySQL 方言：前一个是偏移量）
    match = _LIMIT_COMMA_RE.search(text)
    if match is not None:
        count = min(int(match.group(2)), max_limit)
        return f"{text[:match.start()]} LIMIT {int(match.group(1))}, {count}"

    # 5.3 LIMIT count
    match = _LIMIT_COUNT_RE.search(text)
    if match is not None:
        return f"{text[:match.start()]} LIMIT {min(int(match.group(1)), max_limit)}"

    # 5.4 完全没有 LIMIT（例如 COUNT(*)）——补一个上限
    return f"{text} LIMIT {max_limit}"
