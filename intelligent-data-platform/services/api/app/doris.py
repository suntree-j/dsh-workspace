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

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Sequence

import mysql.connector

from .config import Settings
from .sqlguard import validate_select

__all__ = ["DorisClient", "DorisUnavailable", "QueryResult", "jsonify_value"]


class DorisUnavailable(RuntimeError):
    """Doris 不可用（网络/认证/超时）。接口层会把它映射成 503。"""


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
        except mysql.connector.Error as exc:  # 认证失败 / 网络不可达 / 超时
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
