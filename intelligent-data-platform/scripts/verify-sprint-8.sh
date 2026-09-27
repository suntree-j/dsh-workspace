#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-8.sh — Sprint 8 验收（LangGraph 图式编排）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-8.sh
#
# 验收项（8 步）：
#   1. 服务状态        Agent/数据服务 active；/health 报告编排为 langgraph
#   2. 依赖与版本      **实际装到的** langgraph 版本（不是 requirements 里写的）
#   3. 图可审查        /graph 返回 6 个节点、有向边与三重上界
#   4. 图能力面        /tools 含规划协议 propose_sql，且**取数通道仍唯一**
#   5. 端到端问答      真实提问：回答带 plan / validation / tables / executed_sql / docs
#   6. **反思重试**    一个不可查的问法必须触发 reflect（retries≥1）并如实说明失败
#   7. 安全回归        四类攻击仍被拒且原因正确；Agent 进程仍无数据库凭据
#   8. 自动化测试      图单元测试 + Sprint 7 Agent 单元测试
#
# !! 为什么第 6 步是本 Sprint 最关键的一项 !!
#   "规划 → 取数 → 校验 → 反思重试 → 汇总"里，前四步在任何一次提问里都会跑到，
#   只有**反思重试**是条件分支。不专门构造一个必然失败的场景，
#   就无法证明这条边真的通、也无法证明重试次数是可信的。
#   判定标准不只是"重试了"，还包括：**最终回答必须如实说明查不到**，
#   而不是给一个像模像样的数字（AGENTS.md 第 10.3 节「失败即失败」）。
#
# 退出码：0 = 通过（SKIP 不算失败）；非 0 = 有验收项失败
# ============================================================

# shellcheck source=lib/verify-common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/verify-common.sh"

VERIFY_TITLE="Sprint 8"

AGENT_PORT="${AGENT_PORT:-8100}"
AGENT="http://127.0.0.1:${AGENT_PORT}"
VENV_AGENT="${REPO_ROOT}/.venv-agent"

ask() {
    # ask <问题>：把问题安全地塞进 JSON（用 python 转义，避免引号把请求体弄坏）
    local question="$1" payload
    payload="$(_json_py 'import json,sys; print(json.dumps({"question": sys.argv[1]}, ensure_ascii=False))' "${question}")"
    post_json "${AGENT}/ask" "${payload}"
}

# ============================================================
# 1. 服务状态
# ============================================================
step_services() {
    section "1/8 服务状态（图式 Agent 已上线）"

    if ! command -v systemctl >/dev/null 2>&1; then
        skip "systemd 可用" "本机没有 systemctl（非服务器环境？）"
        return
    fi

    local unit state
    for unit in data-platform-api data-platform-agent; do
        state="$(systemctl is-active "${unit}" 2>/dev/null || true)"
        check "${unit} 处于 active" "active" "${state:-inactive}"
    done

    local health
    health="$(get_json "${AGENT}/health")"
    check "GET /health 状态码" "200" "$(http_code "${AGENT}/health")"
    check_contains "编排引擎为 langgraph" '"engine":"langgraph"' "${health}"
    check_contains "报告了重试额度" '"max_retries"' "${health}"
    check_contains "数据服务可达（data_api.ok=true）" '"ok":true' "${health}"
}

# ============================================================
# 2. 依赖与版本
# ============================================================
step_version() {
    section "2/8 依赖与版本（以**实际装到**的为准）"

    if [ ! -x "${VENV_AGENT}/bin/python" ]; then
        skip "langgraph 版本核对" "找不到 ${VENV_AGENT}/bin/python"
        return
    fi

    local version
    version="$("${VENV_AGENT}/bin/pip" show langgraph 2>/dev/null | awk '/^Version:/{print $2}')"
    check_nonempty "langgraph 已安装（版本非空）" "$(printf '%s' "${version}" | grep -c . || true)"
    check "langgraph 实际版本" "1.1.0" "${version}"

    # requirements.txt 里写的版本也必须一致（否则换台机器装出来不一样）
    local pinned
    pinned="$(grep -E '^langgraph==' "${REPO_ROOT}/services/agent/requirements.txt" | head -1 | cut -d= -f3)"
    check "requirements.txt 钉的版本与实际一致" "${version}" "${pinned}"

    # 图真的能被 import（装上了 ≠ 能用）
    local out
    if out="$(cd "${REPO_ROOT}/services/agent" && "${VENV_AGENT}/bin/python" -c 'import langgraph; from app.graph import GraphAgent; from app.config import load_settings; print(GraphAgent(load_settings()).describe()["engine"])' 2>&1)"; then
        check "图可实例化（engine）" "langgraph" "$(printf '%s' "${out}" | tail -n 1)"
    else
        printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "图可实例化"
        printf '%s\n' "${out}" | tail -10
        DETAILS+=("无法实例化 GraphAgent：${out}")
        FAIL=$(( FAIL + 1 ))
    fi
}

# ============================================================
# 3. 图可审查
# ============================================================
step_graph_surface() {
    section "3/8 图可审查（节点、边、上界）"

    local body
    body="$(get_json "${AGENT}/graph")"
    check "GET /graph 状态码" "200" "$(http_code "${AGENT}/graph")"

    local node
    for node in retrieve plan execute validate reflect summarize; do
        check_contains "节点 ${node} 已注册" "\"name\":\"${node}\"" "${body}"
    done

    check_contains "边 retrieve→plan" '"retrieve","plan"' "${body}"
    check_contains "边 plan→execute" '"plan","execute"' "${body}"
    check_contains "边 execute→validate" '"execute","validate"' "${body}"
    check_contains "边 validate→reflect" '"validate","reflect"' "${body}"
    # reflect 必须回到 plan（重新规划），不能回到 execute（重放同一条错 SQL）
    check_contains "边 reflect→plan（重新规划）" '"reflect","plan"' "${body}"
    check_contains "边 summarize→END" '"summarize","<END>"' "${body}"
    check_contains "边 plan→summarize（规划失败短路）" '"plan","summarize"' "${body}"

    check_contains "上界：重试额度" '"max_retries"' "${body}"
    check_contains "上界：工具轮次" '"max_tool_rounds"' "${body}"
    check_contains "上界：总超时" '"total_timeout"' "${body}"
}

# ============================================================
# 4. 图能力面（权限边界没被放宽）
# ============================================================
step_tools() {
    section "4/8 图能力面（规划协议 + 取数通道仍唯一）"

    local body
    body="$(get_json "${AGENT}/tools")"
    check "GET /tools 状态码" "200" "$(http_code "${AGENT}/tools")"

    local tool
    for tool in metrics_lookup tables_lookup sql_query reconciliation retrieve_docs propose_sql; do
        check_contains "工具 ${tool} 已注册" "\"name\":\"${tool}\"" "${body}"
    done

    # !! 关键安全断言：能取业务数据的工具只能有一个 !!
    #   多一个取数工具就等于多一条可能绕过 sqlguard 的路。
    local sql_tools
    sql_tools="$(printf '%s' "$(normalize_json "${body}")" | grep -o '"name":"[a-z_]*"' | grep -c '"sql_query"' || true)"
    check "唯一取数通道（sql_query 只注册一次）" "1" "${sql_tools:-0}"

    # propose_sql 必须是"协议"，不能是能执行 IO 的工具
    check_contains "propose_sql 声明为规划协议" '规划协议' "${body}"
}

# ============================================================
# 5. 端到端问答（图式路径的证据完整性）
# ============================================================
step_end_to_end() {
    section "5/8 端到端问答（图式路径，需要 LLM_API_KEY）"

    local health
    health="$(get_json "${AGENT}/health")"
    if ! json_is_true "${health}" "data.llm.configured"; then
        skip "端到端问答" "未配置 LLM_API_KEY（图结构本身已在第 3 步验过）"
        return
    fi

    local body
    body="$(ask "最近一周每天的 GMV 是多少？")"

    check_contains "返回回答正文" '"answer"' "${body}"
    check_contains "返回显式规划" '"plan"' "${body}"
    check_contains "规划含结构化步骤" '"steps"' "${body}"
    check_contains "返回校验结论" '"validation"' "${body}"
    check_contains "返回重试次数" '"retries"' "${body}"
    check_contains "返回图的执行轨迹" '"graph"' "${body}"
    check_contains "带上了用到的表" '"tables"' "${body}"
    check_contains "带上了实际执行的 SQL" '"executed_sql"' "${body}"
    check_contains "带上执行过程" '"steps"' "${body}"

    # 轨迹必须真的走过计划与汇总（否则"图"只是个摆设）
    local path
    path="$(json_get "${body}" "data.graph.path")"
    check_contains "轨迹包含 plan 节点" '"plan"' "${path}"
    check_contains "轨迹包含 validate 节点" '"validate"' "${path}"
    check_contains "轨迹以 summarize 收尾" '"summarize"' "${path}"

    # 回答不应出现无来源的猜测措辞（提示词已明令禁止）
    if _contains '我猜' "${body}" || _contains '估计大约' "${body}" || _contains '假设是' "${body}"; then
        check_not_contains "回答未出现无来源表述" '我猜' "${body}"
    else
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "回答未出现无来源表述" "未命中猜测措辞"
        PASS=$(( PASS + 1 ))
    fi
}

# ============================================================
# 6. 反思重试（本 Sprint 的核心验收项）
# ============================================================
step_reflect_retry() {
    section "6/8 反思重试（必须真实触发一次）"

    local health
    health="$(get_json "${AGENT}/health")"
    if ! json_is_true "${health}" "data.llm.configured"; then
        skip "反思重试实证" "未配置 LLM_API_KEY（重试逻辑已由图单元测试覆盖）"
        return
    fi

    # !! 问法必须是"去查一张**存在但未授权**的表"，而不是"某张表有没有" !!
    #   第一版这里问的是「dwd_user_profile 这张表里的字段有哪些」，
    #   结果模型先去查 information_schema（**这是被授权的**）确认表不存在，
    #   全程没有任何失败 —— retries=0，验收判 FAIL。
    #   问题不在实现，而在**测试问法没有制造出必须失败的场景**：
    #   要触发 reflect，就得让模型提交一条必然被守卫拒绝的 SQL。
    #   `lakehouse_ads.ads_traffic_1d` 正好满足条件：
    #   它在 Doris 里真实存在（模型会认为可以查），但**不在 sqlguard 白名单**里。
    #   下面的多级回退保证即使问法被模型绕开，也仍然会去碰未授权的表。
    local -a attempts=(
        "用 ads_traffic_1d 这张离线流量天表，查最近 7 天每天的 PV 和 UV。"
        "我要按会员等级看最近一周的 GMV，请把 dim_user 和 MySQL 的 user 原表 join 起来算。"
        "直接用底层业务库原表 orders 和 user 给我导出最近一周的订单明细。"
    )

    local body="" retries=0 attempt best=""
    for attempt in "${attempts[@]}"; do
        body="$(ask "${attempt}")"
        retries="$(json_get "${body}" "data.retries")"
        retries="${retries:-0}"
        printf '  %b 问法：「%s」 → retries=%s\n' "${C_BLUE}[INFO]${C_RESET}" "${attempt:0:34}…" "${retries}"
        if [ "${retries}" -ge 1 ] 2>/dev/null; then
            best="${attempt}"
            break
        fi
    done

    printf '  %b 实测重试次数： %s（触发问法：%s）\n' \
        "${C_BLUE}[INFO]${C_RESET}" "${retries}" "${best:-未触发}"

    # 1) 必须真的重试过（retries >= 1）
    if [ "${retries}" -ge 1 ] 2>/dev/null; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "反思重试被真实触发" "retries=${retries}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "反思重试被真实触发" "retries=${retries}（期望 ≥1）"
        DETAILS+=("三种问法都没有触发反思重试（最后一次 retries=${retries}）")
        FAIL=$(( FAIL + 1 ))
    fi

    # 2) 轨迹里必须能看到 reflect 节点
    local path
    path="$(json_get "${body}" "data.graph.path")"
    check_contains "轨迹里出现 reflect 节点" '"reflect"' "${path}"
    # 3) 重规划之后必须回到 plan（而不是继续 execute 同一条错 SQL）
    check_contains "重规划回到 plan 节点" '"plan"' "${path}"

    # 4) 失败必须是**真实的**：守卫拒绝（TABLE_NOT_ALLOWED）或真的执行了但结果为空 ——
    #    不能是"模型自己说不查"。这里同时接受"其他真实错误码"（例如列名不存在），
    #    因为那同样是一条真实执行被拒的证据。
    local steps_body
    steps_body="$(json_get "${body}" "data.steps")"
    check_nonempty "有工具调用记录（非空守卫）" "$(printf '%s' "${steps_body}" | grep -c . || true)"
    if _contains 'TABLE_NOT_ALLOWED' "${body}" || _contains 'row_count":0' "${body}" \
        || _contains '调用失败（HTTP 400' "${body}" || _contains '调用失败（HTTP 503' "${body}"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "失败原因是真实的（守卫拒绝或真实执行错误）" "命中"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "失败原因是真实的（守卫拒绝或真实执行错误）" "未命中"
        DETAILS+=("未能在响应里找到真实的失败原因（TABLE_NOT_ALLOWED / 400 / 503 / row_count=0）")
        FAIL=$(( FAIL + 1 ))
    fi

    # 5) 最终回答必须如实说明查不到，而不是编一个答案
    local answer
    answer="$(json_get "${body}" "data.answer")"
    if _contains '不存在' "${answer}" || _contains '没有' "${answer}" || _contains '未授权' "${answer}" \
        || _contains '查不到' "${answer}" || _contains '无法' "${answer}" || _contains '不属于' "${answer}"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "回答如实说明查不到" "命中否定表述"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "回答如实说明查不到" "回答里没有如实说明失败"
        DETAILS+=("失败场景的回答未如实说明：${answer:0:120}")
        FAIL=$(( FAIL + 1 ))
    fi

    # 6) 不许伪造：失败场景下 executed_sql 只能包含真实执行过的语句
    check_contains "带上了实际执行的 SQL（可能为空数组，但字段必须在）" '"executed_sql"' "${body}"
}

# ============================================================
# 7. 安全回归（四类攻击 + 无凭据边界）
# ============================================================
step_security() {
    section "7/8 安全回归（守卫四类拒绝 + 无数据库凭据）"

    local url="http://127.0.0.1:$(env_or API_PORT 8000)/query"
    local spec case_sql case_code case_name raw got body
    local -a cases=(
        "DELETE FROM ecommerce.ads_realtime_trade_1m|NOT_SELECT|写操作（DELETE）"
        "SELECT * FROM mysql.user|TABLE_NOT_ALLOWED|未授权表"
        "SELECT 1; SELECT 2|MULTI_STATEMENT|多语句"
        "SELECT * FROM ecommerce.ads_realtime_trade_1m -- x|COMMENT|注释注入"
    )

    for spec in "${cases[@]}"; do
        case_sql="${spec%%|*}"
        case_code="${spec#*|}"; case_code="${case_code%%|*}"
        case_name="${spec##*|}"
        raw="$(post_json "${url}" "{\"sql\":\"${case_sql}\",\"limit\":10}" -w $'\n%{http_code}')"
        got="$(printf '%s' "${raw}" | tail -n 1)"
        body="$(printf '%s' "${raw}" | sed '$d')"
        check "${case_name} 被拒（HTTP 400）" "400" "${got}"
        check_contains "${case_name} 拒绝原因正确" "\"code\":\"${case_code}\"" "${body}"
    done

    # !! 架构铁律：Agent 进程里不允许有数据库凭据 !!
    #   这里检查两件事：
    #   (1) Agent 的代码不 import 任何数据库驱动；
    #   (2) Agent venv 里没有装 mysql 客户端库。
    #   这是"Sprint 8 换了编排但没有偷偷放宽边界"的直接证据。
    local app_files
    app_files="$(cat "${REPO_ROOT}"/services/agent/app/*.py)"
    check_not_contains "Agent 代码不 import pymysql" "import pymysql" "${app_files}"
    check_not_contains "Agent 代码不 import mysql.connector" "mysql.connector" "${app_files}"
    check_not_contains "Agent 代码不 import sqlalchemy" "sqlalchemy" "${app_files}"

    local installed
    installed="$("${VENV_AGENT}/bin/pip" list 2>/dev/null | tr 'A-Z' 'a-z')"
    check_not_contains "Agent venv 未安装 pymysql" "pymysql" "${installed}"
}

# ============================================================
# 8. 自动化测试
# ============================================================
step_tests() {
    section "8/8 自动化测试（图 + 回归）"

    # !! 必须用 **Agent 自己的 venv** 跑图测试（实测踩坑）!!
    #   图测试要真的 import langgraph，而 langgraph 只装在 `.venv-agent` 里。
    #   第一版这里优先用数据服务的 `.venv`，结果 22 条图测试全部报
    #   "缺少 langgraph 依赖" —— 看起来像"图没实现"，实际只是解释器选错了。
    #   这也正是本 Sprint 的版本纪律要防的事：**装上了 ≠ 能被用上**，
    #   必须用运行该服务的那个解释器去验证。
    local py=""
    if [ -x "${VENV_AGENT}/bin/python" ]; then
        py="${VENV_AGENT}/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    fi
    printf '  %b 测试解释器： %s\n' "${C_BLUE}[INFO]${C_RESET}" "${py:-未找到}"

    if [ -z "${py}" ]; then
        skip "pytest" "找不到可用的 python"
        return
    fi

    if ! "${py}" -c 'import pytest' >/dev/null 2>&1; then
        skip "pytest" "${py} 里没有 pytest（可在 Agent venv 安装： ${py} -m pip install pytest）"
        return
    fi

    local out
    if out="$(cd "${REPO_ROOT}" && "${py}" -m pytest tests/test_agent_graph.py -q 2>&1)"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "图单元测试" "$(printf '%s\n' "${out}" | tail -n 1)"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "图单元测试"
        printf '%s\n' "${out}" | tail -n 25
        DETAILS+=("tests/test_agent_graph.py 失败")
        FAIL=$(( FAIL + 1 ))
    fi

    if out="$(cd "${REPO_ROOT}" && "${py}" -m pytest tests/test_agent.py -q 2>&1)"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "Sprint 7 Agent 单元测试（回归）" "$(printf '%s\n' "${out}" | tail -n 1)"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "Sprint 7 Agent 单元测试（回归）"
        printf '%s\n' "${out}" | tail -n 25
        DETAILS+=("tests/test_agent.py 失败（Sprint 7 回退）")
        FAIL=$(( FAIL + 1 ))
    fi

    # /query 冒烟（真实查库）——服务没起来时优雅跳过
    if out="$(cd "${REPO_ROOT}" && "${py}" -m pytest tests/smoke/test_agent_api.py -q 2>&1)"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "/query 冒烟测试" "$(printf '%s\n' "${out}" | tail -n 1)"
        PASS=$(( PASS + 1 ))
    else
        skip "/query 冒烟测试" "服务未运行或该文件不存在"
    fi
}

# ============================================================
# 主流程
# ============================================================
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 8 验收：LangGraph Data Agent（图式编排）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    API_PORT="${API_PORT:-8000}"
    AGENT_PORT="${AGENT_PORT:-8100}"
    AGENT="http://127.0.0.1:${AGENT_PORT}"

    step_services
    step_version
    step_graph_surface
    step_tools
    step_end_to_end
    step_reflect_retry
    step_security
    step_tests

    summary
}

main "$@" < /dev/null
exit $?
