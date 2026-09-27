"""Doris 只读访问层。

职责边界：
    本模块是**唯一**与数据库打交道的地方。它只做三件事：
      1. 用只读账号建立连接（口令来自 .env）；
      2. 把语句交给 sqlguard 校验后，用参数绑定执行；
      3. 把结果转换成"前端友好且不丢精度"的 JSON 结构。

为什么 DECIMAL 转成字符串：
    金额字段是 DECIMAL(18,2)。若转成 float，51890375.77 这类值在
    序列化后可能变成 51890375.769999999，前端再做一次求和就会漂移。
    统一按字符串返回，交由前端做展示格式化。

为什么每次查询新建连接（不做连接池）：
    Dashboard 的 QPS 极低（人点一下才查一次），连接建立约 10~30ms；
    换来的是"不会有连接泄漏、不会持有过期连接"的确定性。
    若后续 Agent 并发上来，再引入连接池并在此处集中改造。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

import mysql.connector

from .config import Settings
from .sqlguard import validate_select

__all__ = [
    "DorisClient",
    "DorisQueryError",
    "DorisUnavailable",
    "QueryResult",
    "classify_mysql_error",
    "jsonify_value",
]


class DorisUnavailable(RuntimeError):
    """Doris 不可用（网络/认证/超时）。接口层会把它映射成 503。"""


class DorisQueryError(RuntimeError):
    """语句本身被 Doris 拒绝（语义 / 语法错误）。接口层会把它映射成 400。

    为什么必须与 DorisUnavailable **分开**（本类是为此而引入的）：

        在此之前，`_run()` 把 `cursor.execute()` 抛出的任何
        `mysql.connector.Error` 都包成 DorisUnavailable（503）。
        于是「我 SQL 写错了」（例如列名拼错 → `Unknown column`）与
        「仓库连不上」在接口层**长得一模一样**。

        后果不只是状态码难看：数据问答 Agent 拿到 503 会把它理解为
        **可重试的暂时故障**，于是重试一轮 —— 而重试同一个错 SQL 永远
        不会成功，白白多跑一次 LLM 调用（实测 `/ask` 中位数 19.2 s，
        其中 LLM 占 99.9%）。把"语句错"与"仓库坏了"分开之后，
        调用方能按 400 直接放弃重试、把原文交给模型去改写。

    注意本类**不**继承 DorisUnavailable：一旦继承，任何
    `except DorisUnavailable` 的地方（包括接口层的 503 处理器）
    都会把它一起吃掉，两类错误又会重新混在一起。
    """

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


# ============================================================
# 错误分类：哪些是"仓库不可用"，哪些是"语句写错了"
# ============================================================
#
# !! 判据优先用**错误码**，文案匹配只作为兜底 !!
#   错误码是 Doris / MySQL 协议里稳定的一部分，文案会随版本与语言变
#   （实测同一台 Doris 4.1.4 对不同语句会给出英文原句 + 变体措辞）。
#   只按文案匹配就等于把判断建立在"服务端怎么措辞"上。
#
# 这些码的共同点是：**换一个写法就能成功** —— 表名拼错、列名过时、
#   语法不被支持、权限不足、函数用错。它们都不是"仓库暂时不可用"。
_DORIS_SEMANTIC_ERRNOS = frozenset(
    {
        1054,  # Unknown column 'x' in 'field list'
        1064,  # You have an error in your SQL syntax
        1146,  # Table 'ecommerce.x' doesn't exist
        1105,  # Unknown error / 语义错误（Doris 常把语义错包在这个码里）
        1049,  # Unknown database
        1050,  # Table already exists
        1051,  # Unknown table
        1052,  # Column 'x' in field list is ambiguous
        1055,  # SELECT list is not in GROUP BY clause
        1060,  # Duplicate column name
        1065,  # Query was empty
        1142,  # SELECT command denied to user
        1149,  # 语法错误（Doris 变体）
        # 1203 故意不在表里：它多是 "too many active connections"（OperationalError
        # 抛出），属于**仓库侧**的容量问题 → 503 才是对的。
        1235,  # This version of MySQL doesn't yet support 'x'
        1247,  # Reference 'x' not supported
        1305,  # FUNCTION x does not exist
        # 2013 故意不在表里：Lost connection during query（OperationalError，
        # 协议层走的是 Error 分支）→ 连接类故障，必须是 503。
    }
)

# 无错误码时的兜底文案（一律小写匹配）。只用**明确的语义错误措辞**，
# 不放连接类措辞，避免把"仓库不可用"误判成 400。
_DORIS_SEMANTIC_TEXT = re.compile(
    r"unknown column"
    r"|unknown table"
    r"|table .* doesn't exist"
    r"|table .* does not exist"
    r"|unknown database"
    r"|syntax error"
    r"|sql syntax"
    r"|command denied"
    r"|not supported"
    r"|ambiguous"
    r"|does not exist"
)

# 显式排除：这些文案里会**顺带**出现上面的词，但语义是"仓库不可用"。
_DORIS_UNAVAILABLE_TEXT = re.compile(
    r"can't connect"
    r"|cannot connect"
    r"|connection refused"
    r"|lost connection"
    r"|gone away"
    r"|timed out"
    r"|timeout"
    r"|too many connections"
    r"|no route to host"
    r"|broken pipe"
)


def _looks_like_interface_failure(exc: BaseException) -> bool:
    """连接/协议层故障（接口层抛的），不是语句错。

    判据为什么放在这里：`mysql.connector` 的异常体系里
    `InterfaceError` / `PoolError` / `MySQLInterfaceError` 都表示
    "连接或协议这一层出了问题"，与 SQL 内容无关；它们无论带什么码
    都应当是 503。这样分类就不必依赖"码表够不够全"。
    """
    try:
        interface_types: tuple[type[BaseException], ...] = (
            mysql.connector.InterfaceError,
            mysql.connector.PoolError,
            mysql.connector.MySQLInterfaceError,
        )
    except AttributeError:  # pragma: no cover - 驱动版本差异
        interface_types = (mysql.connector.InterfaceError,)
    return isinstance(exc, interface_types)


def _looks_like_unknown_database(exc: BaseException) -> bool:
    """是不是"库名不存在"（错误码 1049 / 对应文案）。

    !! 为什么建连阶段要把它单独摘出来（本地跑测试时实测踩到）!!
        `Unknown database` 在**查询路径**上确实是"你 SQL 写错了"（400），
        但**建连路径**上的库名来自 `.env`（`self.settings.doris_database`），
        不由请求方控制。把它报成 400 会让一次**部署配置错误**
        看起来像调用方的错 —— 状态码把排查方向指反了。
        所以建连阶段显式排除它：仍然 503（与改动前一致）。
    """
    errno = getattr(exc, "errno", None)
    try:
        if errno is not None and int(errno) == 1049:
            return True
    except (TypeError, ValueError):  # pragma: no cover
        pass
    return "unknown database" in str(getattr(exc, "msg", "") or exc).lower()


def classify_mysql_error(exc: BaseException) -> str:
    """把一次驱动异常分类成 ``"semantic"`` 或 ``"unavailable"``。

    抽成独立函数是为了**可单测**：分类逻辑是本模块唯一"会改变对外契约"
    的判断，必须能被逐条钉住，而不是只能靠真机试。
    """
    if _looks_like_interface_failure(exc):
        return "unavailable"

    errno = getattr(exc, "errno", None)
    try:
        if errno is not None:
            errno = int(errno)
    except (TypeError, ValueError):  # pragma: no cover - 驱动给了奇怪的值
        errno = None

    if errno in _DORIS_SEMANTIC_ERRNOS:
        return "semantic"

    text = str(getattr(exc, "msg", "") or exc).lower()
    if _DORIS_UNAVAILABLE_TEXT.search(text):
        return "unavailable"
    if _DORIS_SEMANTIC_TEXT.search(text):
        return "semantic"

    # 取不到码、文案也不认识时**保守**判成不可用（保持改动前 503 的行为）。
    # 理由：把"语句错"误判成 503 只是让 Agent 多试一次；
    #       反过来把"仓库挂了"误判成 400 会让 Agent 直接放弃，覆盖面更糟。
    return "unavailable"


def _doris_error_code(errno: int | None) -> str:
    """给出稳定的错误码字符串（调用方按它做分支，不解析文案）。"""
    return f"DORIS_QUERY_ERROR_{errno}" if errno is not None else "DORIS_QUERY_ERROR"


def _raise_query_error(exc: mysql.connector.Error) -> None:
    """把驱动异常翻译成 DorisQueryError（保留原文与错误码）。"""
    errno = getattr(exc, "errno", None)
    try:
        errno = int(errno) if errno is not None else None
    except (TypeError, ValueError):  # pragma: no cover
        errno = None
    detail = f"{exc}（Doris 错误码 {errno}）" if errno is not None else str(exc)
    raise DorisQueryError(
        _doris_error_code(errno),
        "查询语句被数据仓库拒绝（语句错误，不是仓库不可用）；请检查表名/列名/语法后重写",
        detail,
    ) from exc


@dataclass
class QueryResult:
    """一次查询的结果与元信息。"""

    rows: list[dict[str, Any]] = field(default_factory=list)
    sql: str = ""
    elapsed_ms: int = 0

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def first(self) -> dict[str, Any]:
        return self.rows[0] if self.rows else {}


def jsonify_value(value: Any) -> Any:
    """把数据库值转换为 JSON 友好类型（不丢金额精度）。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _jsonify_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: jsonify_value(value) for key, value in row.items()}


class DorisClient:
    """只读 Doris 客户端。所有语句都会经过 SQL 守卫。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # --------------------------------------------------------
    # 连接
    # --------------------------------------------------------
    def _connect(self) -> mysql.connector.MySQLConnection:
        try:
            return mysql.connector.connect(
                host=self.settings.doris_host,
                port=self.settings.doris_port,
                user=self.settings.doris_user,
                password=self.settings.doris_password,
                database=self.settings.doris_database,
                connection_timeout=self.settings.connect_timeout,
                read_timeout=self.settings.query_timeout,
                write_timeout=self.settings.query_timeout,
                autocommit=True,
                # 纯 Python 实现：不依赖 C 扩展，部署更省心（本项目查询量极小）
                use_pure=True,
            )
        except mysql.connector.Error as exc:  # 认证失败 / 网络不可达 / 超时 / 库不存在
            # 建连阶段同样区分两类：`Unknown column` 这类只可能出现在语句里，
            # 但 **`Unknown database` 不在此列** —— 库名来自 `.env`，
            # 不由请求方控制，报成 400 会把部署配置错误说成调用方的错。
            if classify_mysql_error(exc) == "semantic" and not _looks_like_unknown_database(exc):
                _raise_query_error(exc)
            raise DorisUnavailable(
                f"无法连接 Doris（{self.settings.doris_host}:{self.settings.doris_port}，"
                f"用户 {self.settings.doris_user}）：{exc}"
            ) from exc

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def query(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        max_limit: int | None = None,
    ) -> QueryResult:
        """执行一条只读查询。

        Args:
            sql: SELECT 语句（会经过 sqlguard 校验并强制 LIMIT）。
            params: 参数绑定值（绝不拼接进 SQL 字符串）。
            max_limit: 覆盖默认行数上限。
        """
        safe_sql = validate_select(sql, max_limit or self.settings.max_limit)
        return self._run(safe_sql, params)

    def _run(self, safe_sql: str, params: Sequence[Any] | None = None) -> QueryResult:
        """真正执行 SQL。

        仅供本模块内部使用（调用方必须已完成校验）：
        健康检查用的 `SELECT 1` 没有 FROM 子句，会被守卫的"必须有表名"规则拒绝，
        因此这里保留一个**不接受任何外部输入**的内部通道。
        """
        started = time.perf_counter()
        connection = self._connect()
        try:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(safe_sql, tuple(params) if params else None)
                rows = [_jsonify_row(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
        except mysql.connector.Error as exc:
            # 这里必须**分两类**（见 DorisQueryError 的说明）：
            # 真仓库不可用 → DorisUnavailable（503）；
            # 语句被 Doris 拒绝（Unknown column / Table not found / Syntax error ...）
            # → DorisQueryError（400），并保留错误原文，供模型据此改写 SQL。
            if classify_mysql_error(exc) == "semantic":
                _raise_query_error(exc)
            raise DorisUnavailable(f"查询失败：{exc}") from exc
        finally:
            try:
                connection.close()
            except mysql.connector.Error:
                # 关闭失败不影响查询结果，忽略即可（连接会被服务端回收）
                pass

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return QueryResult(rows=rows, sql=safe_sql, elapsed_ms=elapsed_ms)

    def scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        """查询单个标量值（取第一行第一列）。"""
        result = self.query(sql, params, max_limit=1)
        if not result.rows:
            return None
        return next(iter(result.rows[0].values()))

    def ping(self) -> int:
        """连通性探测，返回耗时毫秒（语句为服务内置常量，无外部输入）。"""
        return self._run("SELECT 1 AS ok").elapsed_ms
