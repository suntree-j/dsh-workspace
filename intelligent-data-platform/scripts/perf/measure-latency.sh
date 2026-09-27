#!/usr/bin/env bash
# ============================================================
# scripts/perf/measure-latency.sh — Sprint 12 性能基线采集（只读）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/perf/measure-latency.sh            # 全部 4 节
#   bash scripts/perf/measure-latency.sh api        # 只测只读接口延迟
#   bash scripts/perf/measure-latency.sh realtime-offline
#   bash scripts/perf/measure-latency.sh batch
#   bash scripts/perf/measure-latency.sh agent
#
# !! 这个脚本**只做只读测量**，不跑 Spark、不暂停 Flink !!
#   内存铁律（AGENTS.md §15.5）：本机 16 GB，实时链路常驻约 13.7 GB。
#   性能基线里"批量作业耗时"一节取自**既有日志**，不为此重跑流水线。
#
# 测量方法（为什么这样测）：
#   1. **中位数而不是平均值**：单次延迟受 JIT/页缓存/邻近代码影响很大，
#      平均值会被一次离群值拉走。同一 SQL 取 N 次，报中位数与 min/max。
#   2. **先预热再测**：第一次调用含 Doris 元数据加载与连接建立，
#      它反映的是"冷启动"，不是稳态。冷启动单独报一次。
#   3. **同时报 `elapsed_ms`（服务端自述）与 `wall_ms`（客户端实测）**：
#      两者的差就是 HTTP + JSON 编解码的开销。只看其中一个会得出
#      "接口很快"或"接口很慢"的片面结论。
#   4. **时间分布用 python3 算**，不在 bash 里手算 —— 排序与中位数
#      在 bash 里写必然出错（而错了的表现是"数字看起来正常"）。
#
# 产出：JSON（stdout），由 docs/PERFORMANCE.md 引用。
# ============================================================

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

# !! 为什么性能测量也要先看 .env 在不在 !!
#   本脚本**不改机器状态**，但它会打满只读接口与 Agent。
#   `.env` 缺失意味着服务处于"靠旧进程苟活"的状态（一重启就起不来）：
#   此时压它一把，得到的延迟数字既不可比，也可能把最后那个还能用的进程拖垮。
#   测量前先确认部署是**可用**的，是对被测系统的基本尊重。
if [ ! -f "${REPO_ROOT}/.env" ]; then
    printf '[FAIL] %s/.env 不存在：部署配置缺失，拒绝进行任何性能测量。\n' "${REPO_ROOT}" >&2
    printf '       请先恢复配置；这不是性能问题，是前提问题。\n' >&2
    exit 1
fi
if [ -f "${REPO_ROOT}/scripts/lib/memory-guard.sh" ]; then
    # 内存铁律：先看闸门怎么判断，不自己拍脑袋
    avail_mb="$(awk '/MemAvailable/{printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
    if [ "${avail_mb}" -lt 800 ]; then
        printf '[FAIL] 可用内存仅 %s MB（< 800 MB）：现在做测量会与实时链路抢内存。\n' "${avail_mb}" >&2
        printf '       请等批处理/Spark 作业结束后再测（AGENTS.md §15.5 内存铁律）。\n' >&2
        exit 1
    fi
fi

PY=""
for candidate in "${REPO_ROOT}/.venv-agent/bin/python" "${REPO_ROOT}/.venv/bin/python"; do
    if [ -x "${candidate}" ]; then PY="${candidate}"; break; fi
done
if [ -z "${PY}" ]; then PY="$(command -v python3)"; fi

API="${API_BASE:-http://127.0.0.1:8000}"
AGENT="${AGENT_BASE:-http://127.0.0.1:8100}"
REPEAT="${REPEAT:-7}"

# ------------------------------------------------------------
# 计时一次 HTTP 调用：输出 wall_ms / http_code / elapsed_ms / row_count
#
# !! 为什么用 curl 的 -w 而不是 date +%s%N 前后取差 !!
#   `date` 起两次进程本身就要几毫秒，而本项目要测的量级正是几十毫秒 ——
#   测量工具的开销会直接污染结论。curl 的 `%{time_total}` 是它自己
#   在同一个进程里测的，没有这个偏差。
# ------------------------------------------------------------
time_get() {
    local url="$1"
    curl -s -o /tmp/perf_body.json -w '%{http_code} %{time_total}' --max-time 30 "${url}"
}

time_post() {
    local url="$1" body="$2"
    curl -s -o /tmp/perf_body.json -w '%{http_code} %{time_total}' --max-time 60 \
        -X POST "${url}" -H 'Content-Type: application/json' -d "${body}"
}

# stats <样本文件> —— 用 python3 算中位数/min/max
stats() {
    "${PY}" - "$1" <<'PYEOF'
import json, sys, statistics
try:
    values = [float(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
except FileNotFoundError:
    values = []
if not values:
    print(json.dumps({"n": 0}, ensure_ascii=False))
else:
    print(json.dumps({
        "n": len(values),
        "median_ms": round(statistics.median(values) * 1000, 1),
        "min_ms": round(min(values) * 1000, 1),
        "max_ms": round(max(values) * 1000, 1),
    }, ensure_ascii=False))
PYEOF
}

# measure_get <标签> <url> —— 重复调用并汇总
measure_get() {
    local label="$1" url="$2"
    local samples=/tmp/perf_samples.txt
    : > "${samples}"
    local warmup code t
    read -r code t < <(time_get "${url}") || true
    local i
    for (( i = 0; i < REPEAT; i++ )); do
        read -r code t < <(time_get "${url}") || true
        printf '%s\n' "${t}" >> "${samples}"
    done
    printf '  %-46s %s  (HTTP %s)\n' "${label}" "$(stats "${samples}")" "${code}"
}

# ============================================================
# 1. 只读接口查询延迟
# ============================================================
section_api() {
    echo "### 1. 只读接口查询延迟（中位数，n=${REPEAT}+1 次预热）"
    echo
    echo "路径全部经 **本机回环**（127.0.0.1），不含公网链路 —— 见 PERFORMANCE.md 的说明。"

    # 固定 SQL：三种典型形态，覆盖"点查 / 聚合 / 关联"
    local sql_simple sql_agg sql_join
    sql_simple="SELECT window_start, gmv FROM ecommerce.ads_realtime_trade_1m ORDER BY window_start DESC LIMIT 10"
    sql_agg="SELECT COUNT(*) AS windows, SUM(gmv) AS total_gmv, SUM(order_cnt) AS orders FROM ecommerce.ads_realtime_trade_1m"
    sql_join="SELECT c.category_name, SUM(o.amount) AS gmv FROM ecommerce.dwd_trade_order_detail o JOIN ecommerce.dim_product c ON o.product_id = c.product_id GROUP BY c.category_name ORDER BY gmv DESC LIMIT 10"

    local samples=/tmp/perf_samples.txt

    measure_sql() {
        local label="$1" sql="$2"
        : > "${samples}"
        local out code t elapsed rows
        # 预热
        out="$(time_post "${API}/query" "{\"sql\": \"${sql}\", \"limit\": 200}")"
        local i
        for (( i = 0; i < REPEAT; i++ )); do
            out="$(time_post "${API}/query" "{\"sql\": \"${sql}\", \"limit\": 200}")"
            code="${out%% *}"; t="${out##* }"
            printf '%s\n' "${t}" >> "${samples}"
        done
        elapsed="$("${PY}" -c 'import json,sys; print(json.load(open("/tmp/perf_body.json",encoding="utf-8"))["data"]["elapsed_ms"])' 2>/dev/null || echo '?')"
        rows="$("${PY}" -c 'import json,sys; print(json.load(open("/tmp/perf_body.json",encoding="utf-8"))["data"]["row_count"])' 2>/dev/null || echo '?')"
        printf '  %-30s %s\n' "${label}" "$(stats "${samples}")"
        printf '  %-30s 服务端 elapsed_ms=%s  行数=%s  HTTP=%s\n' "" "${elapsed}" "${rows}" "${code}"
    }

    measure_sql "点查（10 行明细）" "${sql_simple}"
    measure_sql "聚合（全量 SUM/COUNT）" "${sql_agg}"
    measure_sql "两表关联 + GROUP BY" "${sql_join}"

    echo
    echo "固定的只读接口与元数据接口："
    measure_get "GET /overview" "${API}/overview"
    measure_get "GET /metrics/trade?limit=60" "${API}/metrics/trade?limit=60"
    measure_get "GET /funnel?window_limit=1440" "${API}/funnel?window_limit=1440"
    measure_get "GET /meta/metrics（口径字典）" "${API}/meta/metrics"
    measure_get "GET /meta/tables（表结构）" "${API}/meta/tables"
    measure_get "GET /batch/reconcile（批流对账）" "${API}/batch/reconcile"
}

# ============================================================
# 2. 实时侧 vs 离线侧
# ============================================================
section_realtime_offline() {
    echo
    echo "### 2. 实时侧（ecommerce，1 分钟窗口）vs 离线侧（lakehouse_ads，按天）"
    echo
    echo "为什么这是**同口径**的比较：两侧的表结构由同一份 sql/metadata/metrics.md 定义，"
    echo "差别只在粒度与数据来源（Flink 实时 vs Spark 批量）。"
    echo

    local samples=/tmp/perf_samples.txt
    local cases=(
        "实时 1 分钟窗口聚合|SELECT COUNT(*) AS windows, SUM(gmv) AS total_gmv FROM ecommerce.ads_realtime_trade_1m"
        "离线 按天聚合|SELECT COUNT(*) AS days, SUM(gmv) AS total_gmv FROM lakehouse_ads.ads_batch_trade_1d"
        "实时 类目排行|SELECT category_name, SUM(gmv) AS gmv FROM ecommerce.ads_realtime_category_1m GROUP BY category_name ORDER BY gmv DESC LIMIT 10"
        "离线 类目排行|SELECT category_name, SUM(gmv) AS gmv FROM lakehouse_ads.ads_batch_category_1d GROUP BY category_name ORDER BY gmv DESC LIMIT 10"
        "实时 明细扫描|SELECT order_id, amount FROM ecommerce.dwd_trade_order_detail ORDER BY order_id DESC LIMIT 200"
    )

    local item label sql out code t elapsed rows
    for item in "${cases[@]}"; do
        label="${item%%|*}"; sql="${item##*|}"
        : > "${samples}"
        out="$(time_post "${API}/query" "{\"sql\": \"${sql}\", \"limit\": 200}")"
        for (( i = 0; i < REPEAT; i++ )); do
            out="$(time_post "${API}/query" "{\"sql\": \"${sql}\", \"limit\": 200}")"
            code="${out%% *}"; t="${out##* }"
            printf '%s\n' "${t}" >> "${samples}"
        done
        elapsed="$("${PY}" -c 'import json; print(json.load(open("/tmp/perf_body.json",encoding="utf-8"))["data"]["elapsed_ms"])' 2>/dev/null || echo '?')"
        rows="$("${PY}" -c 'import json; print(json.load(open("/tmp/perf_body.json",encoding="utf-8"))["data"]["row_count"])' 2>/dev/null || echo '?')"
        printf '  %-24s %s\n' "${label}" "$(stats "${samples}")"
        printf '  %-24s 服务端 elapsed_ms=%s 行数=%s HTTP=%s\n' "" "${elapsed}" "${rows}" "${code}"
    done
}

# ============================================================
# 3. 批量作业耗时（**取自既有记录**，不重跑流水线）
# ============================================================
section_batch() {
    echo
    echo "### 3. 批量作业耗时（取自既有 Airflow 运行记录；**未为此重跑流水线**）"
    echo
    echo "内存铁律（AGENTS.md §15.5）：本机 16 GB、实时链路常驻约 12~13.7 GB，"
    echo "因此批量耗时**从既有记录取**，不新跑整条流水线。"
    echo

    # !! 为什么改从**元数据库**取，而不是读任务日志 !!
    #   第一版读 `/opt/data-platform/airflow/logs/dag_id=...` 下的任务日志首尾时间戳。
    #   该目录是 `.gitignore` 覆盖的**机器状态**，在本轮 `.env` 事故中随旧树一起丢失，
    #   全量同步并不会恢复它（仓库里只有 `airflow/dags`）。
    #   于是这一节会 `[SKIP] 找不到 Airflow 任务日志目录` —— 而"跳过"会被误读成
    #   "没有批量作业可测"，掩盖掉"其实只是日志没了"。
    #
    #   `task_instance` 表里的 `duration` 是**更权威**的来源（Airflow 自己算的），
    #   而且不受日志清理策略影响。日志可以用来查"为什么慢"，
    #   但"慢了多少"应当问元数据库。
    local dagdir=/opt/data-platform/airflow/logs/dag_id=offline_lakehouse_pipeline
    if [ -d "${dagdir}" ]; then
        echo "（检测到任务日志目录，按其首尾时间戳列出；权威耗时仍以元数据库为准）"
        local run
        for run in $(ls -1 "${dagdir}" | sort | tail -2); do
            printf '  运行 %s\n' "${run}"
            local task f start end dur
            for task in $(ls -1 "${dagdir}/${run}" | sort); do
                f="$(ls -1t "${dagdir}/${run}/${task}"/attempt=*.log 2>/dev/null | head -1 || true)"
                [ -z "${f}" ] && continue
                start="$(sed -n '1s/^\[*//p' "${f}" | cut -c1-19)"
                end="$(grep -oE '^\[?[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}' "${f}" | tail -1 | tr -d '[')"
                dur="$("${PY}" -c '
import sys
from datetime import datetime
def parse(s):
    s = s.strip().strip("[]").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
a, b = parse(sys.argv[1]), parse(sys.argv[2])
print("%.1f" % ((b - a).total_seconds()) if a and b else "?")
' "${start}" "${end}")"
                printf '    %-20s %-20s → %-20s  %ss\n' "${task}" "${start}" "${end}" "${dur}"
            done
        done
        echo
    else
        printf '  %b 任务日志目录不存在（%s）—— 改从 Airflow 元数据库读取\n' \
            "${C_YELLOW}[注意]${C_RESET}" "${dagdir}"
        printf '        该目录属机器状态，不在仓库里；元数据库中的 duration 才是权威来源。\n'
    fi

    echo
    echo "  --- 元数据库 task_instance（权威耗时）---"
    "${PY}" - <<'PYEOF' 2>&1 || echo "  （元数据库查询失败，请检查 airflow 库是否可达）"
from __future__ import annotations

import subprocess


def query(sql: str) -> list[list[str]]:
    """在 mysql 容器内执行只读查询；口令由容器环境提供，不落盘、不回显。"""
    proc = subprocess.run(
        ["docker", "exec", "-i", "mysql", "sh", "-c",
         'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B airflow -e "$0"', sql],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:300])
    return [line.split("\t") for line in proc.stdout.splitlines() if line.strip()]


DAG = "offline_lakehouse_pipeline"

cols = {row[0] for row in query("SHOW COLUMNS FROM task_instance;")}
run_col = "run_id" if "run_id" in cols else "dag_run_id"  # Airflow 3 用 run_id

runs = query(
    f"SELECT DISTINCT {run_col} FROM task_instance WHERE dag_id='{DAG}' "
    f"ORDER BY {run_col} DESC LIMIT 2;"
)
if not runs:
    print("  元数据库里没有该 DAG 的运行记录。")
    raise SystemExit(0)

for (run_id,) in runs:
    print(f"\n  运行 {run_id}")
    rows = query(
        "SELECT task_id, state, "
        "DATE_FORMAT(start_date,'%Y-%m-%d %H:%i:%s'), "
        "DATE_FORMAT(end_date,'%Y-%m-%d %H:%i:%s'), ROUND(duration,1) "
        f"FROM task_instance WHERE dag_id='{DAG}' AND {run_col}='{run_id}' "
        "ORDER BY start_date;"
    )
    total = 0.0
    for task_id, state, start, end, duration in rows:
        try:
            seconds = float(duration)
        except ValueError:
            seconds = 0.0
        total += seconds
        print(f"    {task_id:<20} {state:<12} {start} → {end}  {seconds:>8.1f}s")
    print(f"    {'耗时合计':<20} {'':<12} {'':<21} {total:>8.1f}s")
PYEOF
}

# ============================================================
# 4. Agent 单次问答端到端（分解到 检索 / 规划 / 取数 / 汇总）
# ============================================================
section_agent() {
    echo
    echo "### 4. Agent 单次问答端到端耗时（分解）"
    echo
    echo "分解方法（都能从**可核对的接口**上量到，不靠猜）："
    echo "  - 总量       POST /ask 的客户端实测 wall time + 响应里的 elapsed_ms"
    echo "  - 取数       POST /query 单独打同一条 SQL（与图里 execute 节点同一个接口）"
    echo "  - 检索       GET /api/retrieve 单独打同一句问题（与图里 retrieve 节点同一段逻辑）"
    echo "  - 规划+汇总  = 总量 − 检索 − 取数（两次 LLM 调用），LLM 延迟不可单独测"
    echo

    local question="${QUESTION:-最近一周每天的 GMV 是多少？}"
    local samples=/tmp/perf_samples.txt

    # 4.1 总量
    : > "${samples}"
    local body out code t i
    out="$(curl -s -o /tmp/perf_ask.json -w '%{http_code} %{time_total}' --max-time 180 \
        -X POST "${AGENT}/ask" -H 'Content-Type: application/json' \
        -d "{\"question\": \"${question}\"}")" || true
    code="${out%% *}"
    for (( i = 0; i < 2; i++ )); do
        out="$(curl -s -o /tmp/perf_ask.json -w '%{http_code} %{time_total}' --max-time 180 \
            -X POST "${AGENT}/ask" -H 'Content-Type: application/json' \
            -d "{\"question\": \"${question}\"}")" || true
        printf '%s\n' "${out##* }" >> "${samples}"
    done
    printf '  %-30s %s  HTTP=%s\n' "POST /ask 总耗时" "$(stats "${samples}")" "${code}"
    "${PY}" - <<'PYEOF'
import json
try:
    d = json.load(open("/tmp/perf_ask.json", encoding="utf-8"))["data"]
except Exception as exc:
    print("    （无法解析 /ask 响应：%s）" % exc)
    raise SystemExit(0)
print("    引擎=%s  服务端 elapsed_ms=%s  重试=%s  执行 SQL 条数=%s"
      % (d.get("engine"), d.get("elapsed_ms"), d.get("retries"), len(d.get("executed_sql") or [])))
sql = (d.get("executed_sql") or [""])[0]
print("    实际执行： %s" % (sql[:160] or "（无）"))
PYEOF

    # 4.2 取数（单独打同一条 SQL）
    local sql
    sql="$("${PY}" -c 'import json; d=json.load(open("/tmp/perf_ask.json",encoding="utf-8"))["data"]; print((d.get("executed_sql") or [""])[0])' 2>/dev/null || true)"
    if [ -n "${sql}" ]; then
        : > "${samples}"
        out="$(time_post "${API}/query" "{\"sql\": \"${sql}\", \"limit\": 200}")"
        for (( i = 0; i < REPEAT; i++ )); do
            out="$(time_post "${API}/query" "{\"sql\": \"${sql}\", \"limit\": 200}")"
            printf '%s\n' "${out##* }" >> "${samples}"
        done
        printf '  %-30s %s  （与图中 execute 节点同一个接口）\n' "取数 POST /query" "$(stats "${samples}")"
    fi

    # 4.3 检索（单独打同一句问题）
    local q
    q="$("${PY}" -c 'import urllib.parse; print(urllib.parse.quote("'"${question}"'"))' 2>/dev/null || true)"
    : > "${samples}"
    out="$(time_get "${AGENT}/api/retrieve?q=${q}&k=5")"
    for (( i = 0; i < REPEAT; i++ )); do
        out="$(time_get "${AGENT}/api/retrieve?q=${q}&k=5")"
        printf '%s\n' "${out##* }" >> "${samples}"
    done
    printf '  %-30s %s  （与图中 retrieve 节点同一段逻辑）\n' "检索 GET /api/retrieve" "$(stats "${samples}")"
}

# ============================================================
# 主流程
# ============================================================
main() {
    local what="${1:-all}"
    printf '性能基线采集 %s  时间=%s  REPEAT=%s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S%z')" "${what}" "${REPEAT}"
    printf '负载： %s\n' "$(uptime | sed 's/.*load average/load average/')"
    printf '内存： %s\n' "$(free -m | sed -n 2p)"
    echo

    case "${what}" in
        api) section_api ;;
        realtime-offline) section_realtime_offline ;;
        batch) section_batch ;;
        agent) section_agent ;;
        all) section_api; section_realtime_offline; section_batch; section_agent ;;
        *) echo "未知小节：${what}（可选 api / realtime-offline / batch / agent / all）" >&2; exit 2 ;;
    esac
}

main "$@"
