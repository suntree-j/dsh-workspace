"""Doris 错误分类：**语句错（400）** 与 **仓库不可用（503）** 必须分开。

## 这个文件在防什么

改之前 `services/api/app/doris.py` 的 `_run()` 把 `cursor.execute()` 抛出的
任何 `mysql.connector.Error` 都包成 `DorisUnavailable` → 接口层统一 503。

于是下面两件事在 HTTP 上**长得一模一样**：

| 真实情况 | 改前 | 改后 |
| --- | --- | --- |
| 列名拼错（`Unknown column 'xxx'`，1054） | 503 `DORIS_UNAVAILABLE` | **400** `DORIS_QUERY_ERROR_1054` |
| 表不存在（`Table ... doesn't exist`，1146） | 503 `DORIS_UNAVAILABLE` | **400** `DORIS_QUERY_ERROR_1146` |
| 语法错（1064） | 503 `DORIS_UNAVAILABLE` | **400** `DORIS_QUERY_ERROR_1064` |
| 连不上 / 超时 / 认证失败 | 503 `DORIS_UNAVAILABLE` | 503 `DORIS_UNAVAILABLE`（不变） |

为什么这不是"状态码洁癖"：数据问答 Agent 拿到 503 会当作**可重试的暂时故障**
退避重试，而重试同一个错 SQL 永远不会成功 —— 只是白跑一轮 LLM
（实测 `/ask` 中位数 19.2 s，LLM 占 99.9%）。400 才是"去改写语句"的信号。

## 为什么这些用例要同时钉"层"与"HTTP 码"

只钉 `classify_mysql_error()` 的返回值，无法证明接口层真的映射成了 400；
只钉 HTTP 码，又无法定位失败在分类还是在处理器。两边都钉，失败信息才指得出责任方。

## 边界（必须一起钉住，否则修一个缺陷引入另一个）

* **接口层故障**（`InterfaceError` / `MySQLInterfaceError`）即使文案里带
  `does not exist` 之类字样，也必须是 503 —— 判据是**异常类型**，不是文案；
* **取不到错误码且文案不认识**时必须保守判成 503（保持改动前的行为）。
  把"仓库挂了"误判成 400 会让 Agent 直接放弃，比多试一次的代价更大。
"""

from __future__ import annotations

import asyncio
from typing import Any

import mysql.connector
import pytest

from services.api.app.doris import (
    DorisClient,
    DorisQueryError,
    DorisUnavailable,
    classify_mysql_error,
)

pytestmark = pytest.mark.unit


def _api_main() -> Any:
    """延迟导入数据服务的 `app.main`（**不要**放到模块级）。

    为什么：本文件大多数用例只依赖 `services.api.app.doris`，
    而 `main` 会连带导入 fastapi / pydantic，并在导入期调用
    `load_settings()`。把它们放在模块级，会让"环境缺 fastapi"表现为
    **整个文件的收集错误**（所有用例一起消失），
    而不是"这一个用例失败" —— 前者很难定位，后者一眼可见。
    """
    from services.api.app import main as api_main

    return api_main


# ============================================================
# 桩：一个只够跑通 DorisClient 的 Settings
# ============================================================
class _StubSettings:
    """最小 Settings 替身。

    为什么不用真的 `Settings`：本文件要证的是**分类与映射**，
    不该依赖 `.env` 是否存在（`load_settings()` 会去读
    `<repo>/.env` 或 `/opt/data-platform/.env`，两台机器上结果不同）。
    """

    doris_host = "doris-fe"
    doris_port = 9030
    doris_user = "agent_ro"
    # 口令只用于喂给被 mock 掉的 connect()；**任何用例都不该打印它**
    doris_password = "not-a-real-password"
    doris_database = "ecommerce"
    connect_timeout = 5
    query_timeout = 10
    max_limit = 200
    default_limit = 100


class _FakeCursor:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.closed = False

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        if self._error is not None:
            raise self._error

    def fetchall(self) -> list[dict[str, Any]]:
        return [{"ok": 1}]

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.closed = False

    def cursor(self, dictionary: bool = False) -> _FakeCursor:  # noqa: ARG002
        return _FakeCursor(self._error)

    def close(self) -> None:
        self.closed = True


def _client_with(monkeypatch: pytest.MonkeyPatch, error: Exception | None) -> DorisClient:
    monkeypatch.setattr(
        mysql.connector, "connect", lambda **_kwargs: _FakeConnection(error)
    )
    return DorisClient(_StubSettings())  # type: ignore[arg-type]


def _handler_response(handler: Any, exc: Exception) -> tuple[int, dict[str, Any]]:
    """直接调用 FastAPI 的异常处理器（不起服务器、不碰数据库）。

    为什么直接调而不是用 TestClient + 真查询：
    本用例的契约是"**这一类异常 → 这个状态码与错误码**"，
    起一个客户端只会让失败信息多绕一层，并把外部依赖引进来。
    """
    response = asyncio.run(handler(None, exc))
    import json

    return response.status_code, json.loads(response.body.decode("utf-8"))


# ============================================================
# 1. 分类函数：逐条钉住判据
# ============================================================
@pytest.mark.parametrize(
    ("errno", "text"),
    [
        (1054, "Unknown column 'no_such_col' in 'field list'"),
        (1146, "Table 'ecommerce.no_such_table' doesn't exist"),
        (1064, "You have an error in your SQL syntax; check the manual"),
        (1105, "errCode = 2, detailMessage = Unknown column 'x'"),
        # 1049（Unknown database）**故意不在这里**：它在查询路径上是 400，
        # 在建连路径上仍是 503，两条路径分开断言（见下面两个专门用例）。
        (1305, "FUNCTION ecommerce.no_such_fn does not exist"),
        (1142, "SELECT command denied to user 'agent_ro'@'%' for table 't'"),
    ],
)
def test_semantic_errnos_classify_as_semantic(errno: int, text: str) -> None:
    """有错误码时**以码为准** —— 文案随版本变，码不会。"""
    assert classify_mysql_error(mysql.connector.Error(msg=text, errno=errno)) == "semantic"


def test_syntax_error_without_errno_falls_back_to_text() -> None:
    """取不到码时按文案兜底（Doris 的 1105 偶尔不给 detail）。"""
    exc = mysql.connector.Error(msg="Syntax error near 'FORM' at line 1")
    assert classify_mysql_error(exc) == "semantic"


@pytest.mark.parametrize(
    "text",
    [
        "Can't connect to MySQL server on 'doris-fe' (111)",
        "Lost connection to MySQL server during query",
        "MySQL Connection not available.",
        "Too many connections",
        "Connection timed out",
    ],
)
def test_connection_failures_classify_as_unavailable(text: str) -> None:
    assert classify_mysql_error(mysql.connector.Error(msg=text)) == "unavailable"


def test_lost_connection_errno_2013_stays_unavailable() -> None:
    """2013 与 1054 都是 OperationalError，**只有码能分开它们**。

    这是本文件最要紧的一条边界：若图省事写成"OperationalError 一律 400"，
    掉线会被报成"你 SQL 写错了"，Agent 就会拿着一条语法完全正确的 SQL
    反复改写 —— 比原来的 503 更难排查。

    !! 这里为什么用 `InterfaceError` 而不是 `OperationalError` !!
        实测（本机 mysql-connector 9.7.0）：`OperationalError` 的
        `__init__(msg, errno)` 第二参签名与 `Error` **不同**，
        `Error(msg="...", errno=2013)` 造出来的对象 `errno` 是 **None** ——
        用错类会让这条用例**看起来在测 2013，实际在测"无码"**。
        驱动在连接丢失时抛的是带 errno 的接口层异常，因此这里照实造一个。
    """
    exc = mysql.connector.InterfaceError(msg="Lost connection to MySQL server during query")
    exc.errno = 2013
    assert exc.errno == 2013, "构造失败：errno 没挂上去，用例会测错东西"
    assert classify_mysql_error(exc) == "unavailable"


def test_interface_error_without_code_is_unavailable() -> None:
    """接口层故障即使文案像语义错，也必须是 503：判据是**异常类型**。"""
    exc = mysql.connector.InterfaceError(
        msg="Cursor is not connected: target table does not exist (protocol)"
    )
    assert classify_mysql_error(exc) == "unavailable"


def test_unknown_error_is_conservatively_unavailable() -> None:
    """既不认识码、也不认识文案 → 保守判成不可用（保持改动前行为）。"""
    assert classify_mysql_error(mysql.connector.Error(msg="something entirely new")) == (
        "unavailable"
    )


def test_unknown_database_is_semantic_on_the_query_path() -> None:
    """在**查询路径**上，"库不存在"是语句错（400）。

    这条与下面那条（建连路径仍 503）合起来才完整：
    同一个错误码在两条路径上语义不同，分类必须由**调用方**决定。
    """
    assert classify_mysql_error(
        mysql.connector.Error(msg="Unknown database 'ecommerce_typo'", errno=1049)
    ) == "semantic"


# ============================================================
# 2. 客户端：异常类型
# ============================================================
def test_query_raises_query_error_on_semantic_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with(
        monkeypatch, mysql.connector.Error(msg="Unknown column 'x' in 'field list'", errno=1054)
    )
    with pytest.raises(DorisQueryError) as excinfo:
        client.query("SELECT x FROM dwd_trade_order_detail")

    err = excinfo.value
    assert err.code == "DORIS_QUERY_ERROR_1054"
    # 错误原文必须保留：Agent 的下一步正是拿它去改 SQL
    assert "Unknown column 'x'" in err.detail


def test_query_raises_unavailable_on_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with(monkeypatch, mysql.connector.Error(msg="Lost connection during query"))
    with pytest.raises(DorisUnavailable):
        client.query("SELECT 1 FROM dwd_trade_order_detail")


def test_query_error_is_not_a_subclass_of_unavailable() -> None:
    """两者**不得**有继承关系。

    一旦 `DorisQueryError` 继承 `DorisUnavailable`，接口层那个 503 处理器
    会把语义错误一起吃掉 —— 两类错误又混回一起，而且从 HTTP 上完全看不出来。
    """
    assert not issubclass(DorisQueryError, DorisUnavailable)


def test_unknown_database_at_connect_time_is_still_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """建连阶段不给"库名写错"开 400 口子。

    这是刻意的取舍（与 `_run` 里的分类不同）：库名来自 `.env`，
    不由请求方控制，把它报成 400 只会让**部署配置错误**看起来像调用方的错。
    """

    def _boom(**_kwargs: Any) -> Any:
        raise mysql.connector.Error(msg="Unknown database 'ecommerce_typo'", errno=1049)

    monkeypatch.setattr(mysql.connector, "connect", _boom)
    with pytest.raises(DorisUnavailable):
        DorisClient(_StubSettings()).ping()  # type: ignore[arg-type]


# ============================================================
# 3. 接口层：HTTP 状态码与 error.code
# ============================================================
def test_http_maps_query_error_to_400() -> None:
    api_main = _api_main()
    status, body = _handler_response(
        api_main._handle_doris_query_error,
        DorisQueryError(
            "DORIS_QUERY_ERROR_1054",
            "查询语句被数据仓库拒绝（语句错误，不是仓库不可用）；请检查表名/列名/语法后重写",
            "1054 (42S22): Unknown column 'no_such_col' in 'field list'（Doris 错误码 1054）",
        ),
    )
    assert status == 400
    assert body["error"]["code"] == "DORIS_QUERY_ERROR_1054"
    # 原文必须在响应体里（Agent 靠它纠错）
    assert "Unknown column 'no_such_col'" in body["error"]["detail"]


def test_http_keeps_unavailable_at_503() -> None:
    api_main = _api_main()
    status, body = _handler_response(
        api_main._handle_doris_unavailable,
        DorisUnavailable("无法连接 Doris（doris-fe:9030）：Can't connect (111)"),
    )
    assert status == 503
    assert body["error"]["code"] == "DORIS_UNAVAILABLE"


def test_handler_codes_are_distinct() -> None:
    """两条处理器必须挂在**不同的异常类**上。

    若哪天把它们合并成一个 `except DorisUnavailable`，
    这个文件里的其它用例仍会通过（它们直接调处理器），
    但真实请求会全部退化成 503 —— 所以这条守着"挂载关系"本身。
    """
    api_main = _api_main()
    handlers = {
        handler.__name__
        for handler in (api_main._handle_doris_query_error, api_main._handle_doris_unavailable)
    }
    assert handlers == {"_handle_doris_query_error", "_handle_doris_unavailable"}
    assert api_main.DorisQueryError is DorisQueryError


# ============================================================
# 4. 端到端（ASGI 内进程）：证明处理器**真的挂在 app 上**
# ============================================================
#
# !! 为什么这一组不能省 !!
#   上面第 3 组是"直接调用处理器"，它们证明不了
#   `@app.exception_handler(DorisQueryError)` 这一行**还在**。
#   把装饰器删掉、或把异常类写错，第 3 组照样全绿，
#   而真实 HTTP 请求会变成 500 —— 这是典型的"测试通过、线上不对"。
#   因此这里走真正的 ASGI 应用（不联网、不起服务器）。
def _client(monkeypatch: pytest.MonkeyPatch, error: Exception) -> Any:
    pytest.importorskip("httpx", reason="TestClient 需要 httpx（测试依赖）")
    from fastapi.testclient import TestClient

    from services.api.app.repository import Repository

    api_main = _api_main()
    if api_main.SETTINGS is None:
        pytest.skip(f"数据服务配置不可用，跳过端到端用例：{api_main.CONFIG_ERROR}")

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    # 拦在 Repository 这一层：不碰数据库、也不碰 SQL 守卫，
    # 只把"查询抛出了哪一类异常"喂给真实的 app。
    monkeypatch.setattr(Repository, "sql_query", _boom)
    return TestClient(api_main.app, raise_server_exceptions=False)


def test_endpoint_returns_400_for_semantic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        DorisQueryError(
            "DORIS_QUERY_ERROR_1146",
            "查询语句被数据仓库拒绝（语句错误，不是仓库不可用）；请检查表名/列名/语法后重写",
            "1146 (42S02): Table 'ecommerce.nope' doesn't exist（Doris 错误码 1146）",
        ),
    )
    resp = client.post("/query", json={"sql": "SELECT 1 FROM dwd_trade_order_detail", "limit": 5})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "DORIS_QUERY_ERROR_1146"
    assert "doesn't exist" in body["error"]["detail"]


def test_endpoint_returns_503_for_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        DorisUnavailable("无法连接 Doris（doris-fe:9030）：Can't connect to MySQL server (111)"),
    )
    resp = client.post("/query", json={"sql": "SELECT 1 FROM dwd_trade_order_detail", "limit": 5})
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "DORIS_UNAVAILABLE"
