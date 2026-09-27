"""MCP 服务唯一的数据出口：**HTTP 调只读数据服务**。

## 为什么这里不是「MCP 直连 Doris」

    MCP 直连数据库会让"最小权限"变成一句口号：
    凭据一旦进了 MCP 进程，能力上限就不再由接口决定，而由代码自觉决定。

    走 HTTP 调 `POST /data/api/query` 之后，MCP 的能力上限被**三件事**锁住：
      1. `services/api/app/sqlguard.py`（仅 SELECT / 表白名单 / 强制 LIMIT）
      2. 只读账号 `agent_ro`（写操作被 Doris 直接拒绝）
      3. 进程边界（MCP 进程里没有凭据，想绕也没有入口）

    因此"不得绕过 sqlguard"这条铁律**不需要靠 MCP 的代码去遵守** ——
    它是结构性的：MCP 除了这个 HTTP 接口之外没有别的取数办法。
    本模块的存在意义就是把这句话写成代码。

## 与 `services/agent/app/tools.py` 的关系（刻意的重复）

    那边也有一份 `_get` / `_post` / `_send`。**不合并**的原因：
      - 两者是**独立部署单元**（各自的 systemd 单元、各自的失败模式），
        合并意味着 MCP 服务要 import Agent 的包 —— 多一条耦合，少一层隔离；
      - 两者的错误语义要求不同：Agent 需要把 4xx 转成"给模型看的提示"，
        MCP 需要把 4xx 转成"给 MCP 客户端看的结构化错误"。
    宁可有两份短而自述的实现，也不要一份要同时满足两种调用方的抽象。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import Settings

__all__ = ["DataServiceError", "DataServiceClient"]


class DataServiceError(RuntimeError):
    """调用只读数据服务失败（网络不可达，或接口返回了错误信封）。"""

    def __init__(self, code: str, message: str, detail: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.status = status

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "http_status": self.status,
        }


class DataServiceClient:
    """只读数据服务的薄客户端。持有的只是一个 URL，没有任何凭据。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # --------------------------------------------------------
    # 底层
    # --------------------------------------------------------
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = ""
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                query = "?" + urllib.parse.urlencode(clean)
        request = urllib.request.Request(
            f"{self.settings.data_api_base}{path}{query}", method="GET"
        )
        return self._send(request)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.settings.data_api_base}{path}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        return self._send(request)

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        """发请求并返回**统一信封**里的 data 部分。

        !! 为什么把 HTTP 错误也解析而不是当成异常就直接抛 !!
            数据服务的业务错误（SQL 被守卫拒绝、表未授权）带结构化
            `{"error": {"code": ..., "message": ...}}`。把 code 原样保留，
            MCP 客户端才能把它当**可读的拒绝原因**处理
            （AGENTS.md §10.3「失败即失败」：不许回退到编造数据）。
            网络不可达与业务拒绝在这里是**两种**错误，不能混成一种。
        """
        try:
            with urllib.request.urlopen(request, timeout=self.settings.api_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return self._unwrap(raw, resp.status)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise self._as_error(raw, exc.code) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DataServiceError(
                "DATA_API_UNREACHABLE",
                f"无法连接只读数据服务 {self.settings.data_api_base}",
                str(exc),
            ) from exc

    @staticmethod
    def _unwrap(raw: str, status: int) -> dict[str, Any]:
        if not raw.strip():
            return {}
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DataServiceError(
                "BAD_RESPONSE", "数据服务返回的不是 JSON", raw[:300], status
            ) from exc
        data = body.get("data")
        if data is None:
            # 数据服务的所有接口都带统一信封；没有 data 说明契约变了，
            # 此时**必须**报错而不是把整个 body 当数据返回（那会把契约破坏
            # 伪装成"查到了数据"）。
            error = body.get("error") or {}
            raise DataServiceError(
                str(error.get("code") or "MISSING_DATA"),
                str(error.get("message") or "响应里没有 data 字段"),
                str(error.get("detail") or raw[:300]),
                status,
            )
        return data

    @staticmethod
    def _as_error(raw: str, status: int) -> DataServiceError:
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return DataServiceError("HTTP_ERROR", f"数据服务返回 HTTP {status}", raw[:300], status)
        error = body.get("error") or {}
        return DataServiceError(
            str(error.get("code") or "HTTP_ERROR"),
            str(error.get("message") or f"数据服务返回 HTTP {status}"),
            str(error.get("detail") or ""),
            status,
        )

    # --------------------------------------------------------
    # 四个只读能力（与 Agent 的能力面严格一致，不多一个）
    # --------------------------------------------------------
    def metrics(self) -> dict[str, Any]:
        """指标口径字典（唯一权威：sql/metadata/metrics.md）。"""
        return self._get("/meta/metrics")

    def tables(self) -> list[dict[str, Any]]:
        """表结构与分层（只覆盖 sqlguard 授权表）。"""
        data = self._get("/meta/tables")
        return list(data.get("rows") or data) if isinstance(data, dict) else list(data)

    def query(self, sql: str, limit: int | None = None) -> dict[str, Any]:
        """执行一条只读 SELECT（经守卫与只读账号）。"""
        payload: dict[str, Any] = {"sql": sql, "limit": limit or self.settings.query_limit}
        return self._post("/query", payload)

    def reconcile(self) -> dict[str, Any]:
        """批流对账结论。"""
        return self._get("/batch/reconcile")
