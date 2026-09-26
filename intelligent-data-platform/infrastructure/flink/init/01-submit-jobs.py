#!/usr/bin/env python3
"""向 Flink SQL Gateway 提交 SQL 作业（纯标准库实现）。

为什么用 Python 而不是 curl：
    flink-jobs 容器基于 python:3.12-alpine，**不含 curl**（实测），
    但自带 python3。用标准库 urllib 可避免依赖外部命令，
    也便于精确处理 JSON 转义（SQL 文本里含引号与 $ 符号）。

为什么用 SQL Gateway 而不是 flink run-application：
    - Gateway 提供 REST 接口，可对每条语句单独判错与重试，幂等可控；
    - Session 模式下所有作业共享同一集群，无需为每个作业打包 JAR；
    - 失败时日志能精确指到哪一条 SQL 出错。

为什么提交完成后要「保持 session 存活」：
    Flink 的作业生命周期绑定在 session 上 —— session 关闭则作业被取消。
    因此本进程会转入保活循环，直到容器被停止。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = os.environ.get("FLINK_GATEWAY_URL", "http://flink-sql-gateway:8083")
JOBMANAGER = os.environ.get("FLINK_JOBMANAGER_URL", "http://flink-jobmanager:8081")
SQL_DIR = Path(os.environ.get("SQL_DIR", "/opt/flink/sql"))
KEEPALIVE_INTERVAL = int(os.environ.get("KEEPALIVE_INTERVAL", "300"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "60"))


def log(msg: str) -> None:
    print(f"[flink-jobs] {msg}", flush=True)


def api(method: str, path: str, payload: dict | None = None,
        base: str | None = None) -> dict:
    """调用 REST API，返回解析后的 JSON。"""
    url = f"{base or GATEWAY}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if body.strip() else {}


def wait_for_gateway(retries: int = 80, interval: int = 3) -> None:
    log(f"等待 SQL Gateway ({GATEWAY}) 就绪 ...")
    for attempt in range(1, retries + 1):
        try:
            info = api("GET", "/v1/info")
            log(f"SQL Gateway 已就绪：{info.get('productName')} {info.get('version')}")
            return
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            if attempt % 5 == 1:
                log(f"尚未就绪，重试 {attempt}/{retries} ... ({exc})")
            time.sleep(interval)
    raise RuntimeError("SQL Gateway 未在预期时间内就绪")


def cancel_running_jobs() -> int:
    """取消集群上所有非终态作业（幂等，可安全重复调用）。

    为什么必须做（实测踩坑，代价很大）：
        Flink SQL Gateway 是 **session 模式**，作业生命周期绑定在 session 上：
        **重启 flink-jobs 容器不会取消它提交过的作业**。旧作业继续占用
        TaskManager slot，导致
          1) 新作业拿不到 slot 而失败：
             NoResourceAvailableException: Could not acquire the minimum required resources
          2) 更隐蔽：旧作业的窗口状态仍在，重放同一批事件时会把新事件
             累加到旧窗口上，指标**翻倍**（实测 PV 合计 39987，事件总数仅 20000）。

        注意：Flink 1.20 的 SQL Gateway **没有** `GET /v1/sessions` 列表接口
        （返回 404），所以不能靠"关 session"清理，必须直接调 JobManager REST。
        手动入口： bash scripts/cancel-flink-jobs.sh
    """
    try:
        resp = api("GET", "/jobs", base=JOBMANAGER)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log(f"无法查询已有作业（忽略）：{exc}")
        return 0

    active = {"RUNNING", "RESTARTING", "CREATED", "RECONCILING",
              "INITIALIZING", "CANCELLING"}
    cancelled = 0
    for job in resp.get("jobs", []) or []:
        jid = job.get("id") or job.get("jid") or ""
        status = job.get("status", "")
        if not jid or status not in active:
            continue
        try:
            api("PATCH", f"/jobs/{jid}?mode=cancel", base=JOBMANAGER)
            cancelled += 1
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            log(f"取消作业 {jid} 失败：{exc}")
    return cancelled


def reset_sessions() -> int:
    """关闭 Gateway 上所有已存在的 session（含其上的作业）。

    为什么必须做（实测踩坑，代价很大）：
        Flink SQL Gateway 是 **session 模式** —— 作业的生命周期绑定在 session 上，
        **重启 flink-jobs 容器不会取消作业**。旧 session 仍活在 Gateway 里，
        旧作业继续占用 TaskManager slot，导致：
          1) 新作业拿不到 slot，直接失败：
             NoResourceAvailableException: Could not acquire the minimum required resources
          2) 更隐蔽：旧作业的窗口状态还在，重放同一批事件时会把新事件
             累加到旧窗口上，指标**翻倍**（实测 PV 合计 39987，事件总数仅 20000）。

        因此每次提交前先清空 session，保证「一个集群只有一套作业」。
        手动清理入口： bash scripts/cancel-flink-jobs.sh
    """
    try:
        resp = api("GET", "/v1/sessions")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log(f"无法列出已有 session（忽略）：{exc}")
        return 0

    handles = resp.get("sessions", []) or []
    closed = 0
    for handle in handles:
        if isinstance(handle, dict):  # 兼容 {"sessionHandle": "..."} 形态
            handle = handle.get("sessionHandle", "")
        if not handle:
            continue
        try:
            api("DELETE", f"/v1/sessions/{handle}")
            closed += 1
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            log(f"关闭 session {handle} 失败：{exc}")
    if closed:
        log(f"已关闭 {closed} 个历史 session（连同其上的作业）")
    return closed


def create_session(retries: int = 20) -> str:
    log("创建 Flink SQL session ...")
    for attempt in range(1, retries + 1):
        try:
            resp = api("POST", "/v1/sessions", {})
            handle = resp.get("sessionHandle")
            if handle:
                log(f"session = {handle}")
                return handle
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            log(f"创建 session 失败（{attempt}/{retries}）：{exc}")
        time.sleep(5)
    raise RuntimeError("无法创建 SQL session")


def split_statements(text: str) -> list[str]:
    """按分号拆分 SQL，跳过注释行与空行。

    说明：本项目的 SQL 文件中不含字符串字面量里的分号，
    因此简单的按行累积 + 分号结尾即可正确拆分。
    """
    statements: list[str] = []
    buf: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).rstrip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def submit(session: str, statement: str) -> tuple[bool, str]:
    """提交单条语句并等待完成。返回 (是否成功, 错误信息)。"""
    try:
        resp = api("POST", f"/v1/sessions/{session}/statements",
                   {"statement": statement})
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}"
    except (urllib.error.URLError, OSError) as exc:
        return False, str(exc)

    op = resp.get("operationHandle")
    if not op:
        return False, f"未返回 operationHandle: {resp}"

    # 轮询直到语句结束
    for _ in range(90):
        try:
            st = api("GET", f"/v1/sessions/{session}/operations/{op}/status")
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            time.sleep(2)
            continue
        status = st.get("status")
        if status == "FINISHED":
            return True, ""
        if status in ("ERROR", "CANCELED", "CLOSED"):
            detail = ""
            try:
                res = api("GET",
                          f"/v1/sessions/{session}/operations/{op}/result/0")
                detail = json.dumps(res, ensure_ascii=False)[:600]
            except Exception:  # noqa: BLE001 - 结果取不到不影响错误上报
                pass
            return False, f"status={status} {detail}"
        time.sleep(2)
    return False, "语句执行超时"


def main() -> int:
    wait_for_gateway()

    # 先清场：保证集群上只有本容器提交的这一套作业
    cancelled = cancel_running_jobs()
    if cancelled:
        log(f"已取消历史作业 {cancelled} 个，等待 slot 释放 ...")
        time.sleep(5)
    else:
        log("集群上没有历史作业，无需清理")
    reset_sessions()

    session = create_session()

    files = sorted(SQL_DIR.glob("*.sql"))
    if not files:
        log(f"警告：{SQL_DIR} 下没有 .sql 文件")

    total = 0
    failed = 0
    for path in files:
        log("-" * 45)
        log(f"执行文件：{path.name}")
        statements = split_statements(path.read_text(encoding="utf-8"))
        for stmt in statements:
            total += 1
            short = " ".join(stmt.split())[:70]
            ok, err = submit(session, stmt)
            if ok:
                log(f"  [OK]   {short} ...")
            else:
                failed += 1
                log(f"  [FAIL] {short} ...")
                log(f"         {err}")

    log("-" * 45)
    log(f"提交完成：共 {total} 条语句，失败 {failed} 条")

    # ------------------------------------------------------------
    # 保活：session 一旦关闭，其上的作业会被取消
    # ------------------------------------------------------------
    log(f"进入 session 保活循环（每 {KEEPALIVE_INTERVAL}s），作业将持续运行")
    while True:
        time.sleep(KEEPALIVE_INTERVAL)
        try:
            api("GET", f"/v1/sessions/{session}")
        except Exception as exc:  # noqa: BLE001 - 心跳失败只告警不退出
            log(f"警告：session 心跳失败（Gateway 可能重启）：{exc}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
