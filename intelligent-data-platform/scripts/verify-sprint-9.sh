#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-9.sh — Sprint 9 验收（RAG + Metadata：检索增强）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-9.sh
#
# 验收项（8 步）：
#   1. 服务状态        两个 systemd 单元 active；检索层已装载（/health.retrieval.ok）
#   2. 检索画像        /retrieval 报告后端=词法、语料条数、**每一路语料的状态**
#   3. 语料完整性      口径指标条目数、指标/约定/事件/分层四类都在，且**逐条带来源**
#   4. 命中可解释      命中带 source/line/score/matched_terms（可点回原文核对）
#   5. **同义词生效**  一个不含 GMV 字样的问法命中 GMV 口径；且负例不乱命中
#   6. 口径类问答      「复购率怎么算」能引到相关口径（无该口径时须如实说明）
#   7. 未引入向量库    requirements.txt / pip list 里都没有 milvus / faiss / es
#   8. 回归            Sprint 7 的 49 项不许回退 + 新增单元测试
#
# !! 为什么第 5 步是本次验收的核心 !!
#   Sprint 9 采用的是**词法检索**（BM25，见 docs/DECISIONS.md ⏳7），
#   它的已知代价就是"用词不同的提问召回不到"。同义词表是唯一的补偿机制，
#   因此"同义词是否真的生效"不能靠读代码判断，必须用一个
#   **不含目标术语字样**的问法去打，并检查响应里确实记录了这次扩展。
#   同时必须有负例：只有正例的话，"把所有词都映射一遍"的坏配置也能全绿。
#
# 退出码：0 = 通过（SKIP 不算失败）；非 0 = 有验收项失败
# ============================================================

# shellcheck source=lib/verify-common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/verify-common.sh"

VERIFY_TITLE="Sprint 9"

AGENT_PORT="${AGENT_PORT:-8100}"
AGENT="http://127.0.0.1:${AGENT_PORT}"

urlencode() {
    _json_py 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1"
}

retrieve() {
    # retrieve <查询词> [k]
    local query="$1" k="${2:-5}"
    get_json "${AGENT}/api/retrieve?q=$(urlencode "${query}")&k=${k}"
}

# ============================================================
# 1. 服务状态
# ============================================================
step_services() {
    section "1/8 服务状态（Agent 与检索层）"

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
    check_contains "报告了检索层状态" '"retrieval"' "${health}"
    check_contains "检索层已就绪（retrieval.ok=true）" '"ok":true' "${health}"
    # 检索不可用时 /ask 仍能跑，但回答里不会再有口径引用 —— 必须在这里拦住
    check_contains "检索后端是词法（词法，无向量库）" '"backend":"lexical-bm25"' "${health}"
}

# ============================================================
# 2. 检索画像
# ============================================================
step_profile() {
    section "2/8 检索画像（语料与后端自述）"

    local body total
    body="$(get_json "${AGENT}/retrieval")"
    total="$(json_get "${body}" "data.docs_total")"

    check "GET /retrieval 状态码" "200" "$(http_code "${AGENT}/retrieval")"
    check_contains "后端自述为词法检索" '"backend":"lexical-bm25"' "${body}"
    check_contains "明确声明没有向量库" '"vector_store":null' "${body}"
    check_contains "报告了同义词表规模与来源" '"synonyms"' "${body}"
    check_contains "同义词表来源可核对" 'synonyms.json' "${body}"

    # 非空守卫：空语料上的一切断言都没有意义
    check_nonempty "语料条数" "${total:-0}"

    # 每一路语料都必须有状态，且**不能静默缺失**
    check_contains "口径文档这一路有状态" 'sql/metadata/metrics.md' "${body}"
    check_contains "事件格式这一路有状态" 'sql/metadata/kafka_topics.md' "${body}"
    check_contains "分层说明这一路有状态" 'layering.md' "${body}"
    check_contains "表结构这一路有状态" 'live_tables' "${body}"

    local metric_count
    metric_count="$(json_get "${body}" "data.counts_by_kind.metric")"
    check_nonempty "指标口径条目数" "${metric_count:-0}"
}

# ============================================================
# 3. 语料完整性（检索不到 = 等于没有）
# ============================================================
step_corpus() {
    section "3/8 语料完整性（四类知识都在）"

    local body hits
    body="$(retrieve "gmv" 10)"
    hits="$(json_len "${body}" "data.hits")"
    check_nonempty "「gmv」的命中条数" "${hits:-0}"

    # 用几个必然存在于语料里的术语，逐条验证检索真的能取到
    local term
    for term in gmv payment_success_rate refund_rate; do
        body="$(retrieve "${term}" 5)"
        check_contains "检索「${term}」命中指标口径" "metric:${term}" "${body}"
    done

    # 事件格式（Sprint 0 基线）也必须可检索
    body="$(retrieve "ORDER_CREATED 事件" 5)"
    check_contains "检索事件类型命中 kafka_topics.md" "kafka_topics.md" "${body}"

    # 分层说明必须可检索
    body="$(retrieve "分层 ODS DWD DWS ADS" 5)"
    check_contains "检索分层命中 layering.md" "layering.md" "${body}"

    # 每条命中都必须有来源文件与行号（可点回原文）
    body="$(retrieve "GMV 怎么算" 5)"
    check_contains "命中带来源文件" '"source":"sql/metadata/metrics.md"' "${body}"
    check_contains "命中带行号（可点回原文）" '"citation":"sql/metadata/metrics.md:' "${body}"
    check_contains "命中带命中词项（可解释）" '"matched_terms"' "${body}"
    check_contains "命中带分数" '"score"' "${body}"
}

# ============================================================
# 4. 命中可解释 + 降级可见
# ============================================================
step_explainable() {
    section "4/8 命中可解释（为什么是它）"

    local body
    body="$(retrieve "客单价怎么算" 3)"
    check_contains "命中带 relevance（归一化分数）" '"relevance"' "${body}"
    check_contains "命中了客单价口径" "metric:avg_order_amount" "${body}"

    # 语料不覆盖的问题必须**如实为空**，而不是硬凑命中（"总能命中点什么"最误导）
    body="$(retrieve "今天天气怎么样适合钓鱼吗" 3)"
    check_contains "不相关问题没有命中（不硬凑）" '"hits":[]' "${body}"

    # 至少有一路语料是"活"的：报告里不能全是失败状态
    body="$(get_json "${AGENT}/retrieval")"
    check_contains "口径语料装载成功（ok）" '"sql/metadata/metrics.md":"ok' "${body}"
}

# ============================================================
# 5. 同义词生效（本 Sprint 的核心验收项）
# ============================================================
step_synonyms() {
    section "5/8 同义词生效（口语问法命中权威口径）"

    # !! 前提断言：问题里不能出现 gmv 字样，否则测的是 BM25 而不是同义词 !!
    local question="最近一周卖了多少钱"
    case "$(printf '%s' "${question}" | tr 'A-Z' 'a-z')" in
        *gmv*)
            check "前提：问法不含 gmv 字样" "不含" "含有（测试无意义）"
            return
            ;;
    esac
    printf '  %b 前提成立：问法「%s」不含 gmv 字样\n' "${C_GREEN}[ OK ]${C_RESET}" "${question}"

    local body
    body="$(retrieve "${question}" 5)"

    # 1) 扩展必须被记录下来（可审计）
    check_contains "同义词扩展已记录" '"expanded_terms"' "${body}"
    check_contains "扩展来自「卖了多少钱」" '"phrase":"卖了多少钱"' "${body}"
    check_contains "该扩展指向 gmv" '"terms":["gmv"' "${body}"
    # 2) 必须真的命中 GMV 口径文档
    check_contains "命中 GMV 口径文档" 'metric:gmv' "${body}"
    check_contains "命中的是 GMV 口径（标题）" 'GMV（gmv）' "${body}"
    # 3) 每条扩展都要带理由（无理由的映射会让检索退化成"总能命中点什么"）
    check_contains "扩展带理由（可审查）" '"reason"' "${body}"

    # 4) 负例：不相关的问法绝不能把 GMV 拉出来
    body="$(retrieve "这个系统的日志文件放在哪个目录" 5)"
    check_not_contains "负例：无关问题不命中 GMV" "metric:gmv" "${body}"

    # 5) 另一个同义词（客单价）也要生效
    body="$(retrieve "平均每单多少钱" 3)"
    check_contains "同义词「平均每单」→客单价生效" '"phrase":"平均每单"' "${body}"
    check_contains "命中客单价口径" "metric:avg_order_amount" "${body}"
}

# ============================================================
# 6. 口径类问答（检索结果真的进了回答链路）
# ============================================================
step_ask_uses_docs() {
    section "6/8 口径类问答（检索结果进入回答链路）"

    local health
    health="$(get_json "${AGENT}/health")"
    if ! json_is_true "${health}" "data.llm.configured"; then
        skip "端到端问答" "未配置 LLM_API_KEY（检索层本身已在第 3~5 步验过）"
        return
    fi

    local body docs_count
    body="$(post_json "${AGENT}/ask" '{"question":"复购率怎么算？"}')"
    docs_count="$(json_len "${body}" "data.docs")"

    check "POST /ask 状态码" "200" "$(http_code -X POST "${AGENT}/ask" \
        -H 'Content-Type: application/json' -d '{"question":"复购率怎么算？"}')"
    check_nonempty "回答携带命中的口径文档条数" "${docs_count:-0}"
    check_contains "回答带 docs（来源可追溯）" '"docs"' "${body}"
    check_contains "docs 带来源文件与行号" '"citation"' "${body}"
    check_contains "回答带 tables" '"tables"' "${body}"
    check_contains "回答带 executed_sql" '"executed_sql"' "${body}"
    # source 里也要有 documents，供看板展示"依据了哪些口径"
    check_contains "source.documents 已返回" '"documents"' "${body}"
}

# ============================================================
# 7. 未引入向量库（方案纪律）
# ============================================================
step_no_vector_store() {
    section "7/8 未引入向量库（方案纪律）"

    local req
    req="$(cat "${REPO_ROOT}/services/agent/requirements.txt")"
    check_not_contains "requirements.txt 无 milvus" "milvus" "${req}"
    check_not_contains "requirements.txt 无 faiss" "faiss" "${req}"
    check_not_contains "requirements.txt 无 elasticsearch" "elasticsearch" "${req}"
    check_not_contains "requirements.txt 无 chromadb" "chromadb" "${req}"
    check_not_contains "requirements.txt 无 sentence-transformers" "sentence-transformers" "${req}"

    local py=""
    if [ -x "${REPO_ROOT}/.venv-agent/bin/python" ]; then
        py="${REPO_ROOT}/.venv-agent/bin/python"
    fi
    if [ -z "${py}" ]; then
        skip "Agent venv 依赖核对" "找不到 ${REPO_ROOT}/.venv-agent/bin/python"
        return
    fi

    local installed
    installed="$("${py}" -m pip list 2>/dev/null | tr 'A-Z' 'a-z')"
    check_not_contains "已安装依赖里没有向量库" "milvus" "${installed}"
    check_not_contains "已安装依赖里没有 faiss" "faiss" "${installed}"
    # 检索是纯标准库实现：装完 langgraph 后也不应有任何"检索专用"新依赖
    check_not_contains "已安装依赖里没有 elasticsearch" "elasticsearch" "${installed}"
    check_nonempty "Agent venv 可列出依赖（非空守卫）" "$(printf '%s' "${installed}" | grep -c . || true)"
}

# ============================================================
# 8. 回归
# ============================================================
step_regression() {
    section "8/8 回归（Sprint 7 不许回退 + 新增单测）"

    # 与 verify-sprint-8.sh 保持一致：优先用 **Agent 自己的 venv**。
    # Agent 的单元测试要 import 图中的 langgraph，而它只装在 `.venv-agent` 里；
    # 用数据服务的 `.venv` 跑会得到"缺少 langgraph"这种与本次验收无关的失败。
    local py=""
    if [ -x "${REPO_ROOT}/.venv-agent/bin/python" ]; then
        py="${REPO_ROOT}/.venv-agent/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    fi

    if [ -z "${py}" ]; then
        skip "pytest" "找不到可用的 python"
        return
    fi
    if ! "${py}" -c 'import pytest' >/dev/null 2>&1; then
        skip "pytest" "${py} 里没有 pytest"
        return
    fi

    local out
    if out="$(cd "${REPO_ROOT}" && "${py}" -m pytest tests/test_retrieval.py -q 2>&1)"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "检索单元测试" "$(printf '%s\n' "${out}" | tail -n 1)"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "检索单元测试"
        printf '%s\n' "${out}" | tail -n 25
        DETAILS+=("tests/test_retrieval.py 失败")
        FAIL=$(( FAIL + 1 ))
    fi

    # Sprint 7 的 Agent 单元测试也必须在（工具集合改动过，需要回归）
    if out="$(cd "${REPO_ROOT}" && "${py}" -m pytest tests/test_agent.py -q 2>&1)"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "Sprint 7 Agent 单元测试（回归）" "$(printf '%s\n' "${out}" | tail -n 1)"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "Sprint 7 Agent 单元测试（回归）"
        printf '%s\n' "${out}" | tail -n 25
        DETAILS+=("tests/test_agent.py 失败（Sprint 7 回退）")
        FAIL=$(( FAIL + 1 ))
    fi

    # 检索是纯函数：单元测试不得依赖外部服务（AGENTS.md 第 8 2 节）
    local test_src
    test_src="$(cat "${REPO_ROOT}/tests/test_retrieval.py")"
    check_in_source "检索测试已声明为单元测试" "${test_src}" 'pytest.mark.unit'
}

# ============================================================
# 主流程
# ============================================================
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 9 验收：RAG + Metadata（词法检索）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    AGENT_PORT="${AGENT_PORT:-8100}"
    AGENT="http://127.0.0.1:${AGENT_PORT}"

    step_services
    step_profile
    step_corpus
    step_explainable
    step_synonyms
    step_ask_uses_docs
    step_no_vector_store
    step_regression

    summary
}

main "$@" < /dev/null
exit $?
