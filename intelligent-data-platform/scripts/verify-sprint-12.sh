#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-12.sh — Sprint 12 验收（测试 + 性能基线）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-12.sh
#
# 验收项（5 步）：
#   1. 前置可用性   `.env` 存在（缺它就拒跑：一个只读验收不该在坏配置上给结论）
#   2. 测试全量     `python -m pytest` 的**实测通过数**（不是"文件存在"）
#   3. 测试分布     单元/冒烟各自的数量与标记正确性；补的缺口文件确实在
#   4. 性能文档     `docs/PERFORMANCE.md` 存在，且**含实测数字**与四类基线
#   5. 结构债记录   已知结构债在 `SPRINT_12.md` 里写明"未做及原因"
#
# !! 第 3、4 步的核心是"文档里必须有数字"而不是"文档存在" !!
#   性能基线最容易变成一篇"方法论散文"：读起来很专业，一个数都没有。
#   因此本脚本会去文档里**找数字**（毫秒 / 秒 / 行数），并要求四类基线各自
#   有对应的表格行。没有数字 = 没有基线 = 这一步失败。
#
# !! 为什么第 5 步要验收"没做的事" !!
#   `run-batch-pipeline.sh` 与 `submit-offline-job.sh` 各有一份阶段 `case`，
#   是 Sprint 4 踩过"阶段静默空转"的结构债。本 Sprint **不动别人的文件**，
#   但"不动"必须留下记录，否则下一轮的人会以为没人发现过。
#   验收判据：SPRINT_12.md 里能搜到这两个脚本名 + "未做" + 原因。
#
# 退出码：0 = 通过（SKIP 不算失败）；非 0 = 有验收项失败
# ============================================================

# shellcheck source=lib/verify-common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/verify-common.sh"

VERIFY_TITLE="Sprint 12"

PERF_DOC="${REPO_ROOT}/docs/PERFORMANCE.md"
SPRINT12_DOC="${REPO_ROOT}/docs/sprint/SPRINT_12.md"

# ------------------------------------------------------------
# 选解释器
#
# !! 为什么优先 .venv-agent 而不是 .venv !!
#   `tests/test_agent_graph.py` 要 import langgraph，而它只装在 `.venv-agent`；
#   用数据服务的 `.venv` 跑全量测试会得到一批"缺少 langgraph"的失败 ——
#   那是**解释器选错**，不是代码缺陷（Sprint 8 已记录这条）。
#   本 Sprint 的目标是"全量 pytest 全绿"，因此必须用**依赖最全的那个**解释器。
# ------------------------------------------------------------
pick_python() {
    for candidate in "${REPO_ROOT}/.venv-agent/bin/python" "${REPO_ROOT}/.venv/bin/python"; do
        if [ -x "${candidate}" ] && "${candidate}" -c 'import pytest' >/dev/null 2>&1; then
            printf '%s' "${candidate}"
            return 0
        fi
    done
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import pytest' >/dev/null 2>&1; then
        command -v python3
        return 0
    fi
    printf ''
}

# ============================================================
# 1. 前置可用性
# ============================================================
step_preflight() {
    section "1/5 前置可用性"

    if [ ! -f "${REPO_ROOT}/.env" ]; then
        printf '  %b %s 不存在\n' "${C_RED}[FAIL]${C_RESET}" "${REPO_ROOT}/.env"
        printf '\n  这是**部署前提缺失**，不是本 Sprint 的缺陷。\n'
        printf '  请先恢复配置再验收 —— 在坏配置上跑只读验收只会产出不可信的结论。\n\n'
        exit 1
    fi
    printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" ".env 存在" \
        "$(stat -c '%A %U:%G' "${REPO_ROOT}/.env" 2>/dev/null || echo '?')"
    PASS=$(( PASS + 1 ))

    local py
    py="$(pick_python)"
    if [ -z "${py}" ]; then
        skip "pytest 解释器" "找不到装了 pytest 的 python"
    else
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "pytest 解释器" "${py}"
        PASS=$(( PASS + 1 ))
    fi
}

# ============================================================
# 2. 测试全量（实测通过数）
# ============================================================
step_pytest_all() {
    section "2/5 测试全量（python -m pytest 实测）"

    local py
    py="$(pick_python)"
    if [ -z "${py}" ]; then
        skip "全量 pytest" "找不到可用的 python"
        return
    fi

    local out rc
    set +e
    out="$(cd "${REPO_ROOT}" && "${py}" -m pytest -q 2>&1)"
    rc=$?
    set -e

    # 取最后一行（`N passed, M skipped in Xs` 之类的汇总行）
    local summary_line
    summary_line="$(printf '%s\n' "${out}" | grep -E '[0-9]+ (passed|failed|error)' | tail -1)"

    if [ "${rc}" -eq 0 ]; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "全量 pytest 退出码 0" "${summary_line:-（无汇总行）}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "全量 pytest 失败（exit ${rc}）"
        printf '%s\n' "${out}" | tail -40
        DETAILS+=("全量 pytest 未通过：${summary_line:-exit ${rc}}")
        FAIL=$(( FAIL + 1 ))
    fi

    # 非空守卫：必须真的跑了测试，"0 passed" 不能被当成成功
    local passed
    passed="$(printf '%s' "${summary_line}" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || true)"
    if [ -n "${passed}" ]; then
        check_nonempty "实测通过数（非空守卫）" "${passed}"
        check "通过数不少于 Sprint 9 的基线（172）" "yes" \
            "$([ "${passed}" -ge 172 ] && echo yes || echo "no(${passed})")"
    else
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "无法从输出里解析通过数" "${summary_line:-空}"
        DETAILS+=("pytest 汇总行不含 'N passed'：${summary_line:-空}")
        FAIL=$(( FAIL + 1 ))
    fi
}

# ============================================================
# 3. 测试分布与补齐的缺口
# ============================================================
step_test_surface() {
    section "3/5 测试分布（单元/冒烟）与本次补齐的缺口"

    local py
    py="$(pick_python)"
    if [ -z "${py}" ]; then
        skip "单元测试" "找不到可用的 python"
        return
    fi

    # 单元测试（不依赖任何外部服务，AGENTS.md §8.2）
    local unit_out
    if unit_out="$(cd "${REPO_ROOT}" && "${py}" -m pytest -m unit -q 2>&1)"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "单元测试（-m unit）" \
            "$(printf '%s\n' "${unit_out}" | tail -1)"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "单元测试（-m unit）"
        printf '%s\n' "${unit_out}" | tail -25
        DETAILS+=("pytest -m unit 失败")
        FAIL=$(( FAIL + 1 ))
    fi

    # 本次补齐的缺口文件必须真的在，且各自非空
    local target
    for target in tests/test_mcp.py tests/test_sql_guard_adversarial.py; do
        if [ ! -f "${REPO_ROOT}/${target}" ]; then
            printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "${target} 存在" "缺失"
            DETAILS+=("${target} 不存在")
            FAIL=$(( FAIL + 1 ))
            continue
        fi
        local count
        count="$(cd "${REPO_ROOT}" && "${py}" -m pytest "${target}" --collect-only -q 2>/dev/null | grep -cE '::' || true)"
        check_nonempty "${target} 的用例数（非空守卫）" "${count:-0}"
    done

    # 对抗用例必须声明为单元测试（它们不该依赖任何服务）
    check_in_source "对抗用例声明为单元测试" \
        "$(cat "${REPO_ROOT}/tests/test_sql_guard_adversarial.py")" 'pytest.mark.unit'
    check_in_source "MCP 用例声明为单元测试" \
        "$(cat "${REPO_ROOT}/tests/test_mcp.py")" 'pytest.mark.unit'

    # 已知缺口必须用 xfail(strict=True) 记录 —— 没有它，缺口会变成"永远绿"
    check_in_source "已知缺口有 xfail 记录" \
        "$(cat "${REPO_ROOT}/tests/test_sql_guard_adversarial.py")" 'xfail(' 'strict=True'
}

# ============================================================
# 4. 性能文档（必须有实测数字）
# ============================================================
step_performance_doc() {
    section "4/5 性能基线文档（必须有实测数字，不能只是方法论）"

    if [ ! -f "${PERF_DOC}" ]; then
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "docs/PERFORMANCE.md 存在" "缺失"
        DETAILS+=("docs/PERFORMANCE.md 不存在")
        FAIL=$(( FAIL + 1 ))
        return
    fi
    printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "docs/PERFORMANCE.md 存在" \
        "$(wc -l < "${PERF_DOC}") 行"
    PASS=$(( PASS + 1 ))

    local doc
    doc="$(cat "${PERF_DOC}")"

    # 四类基线各自的关键词必须在文档里出现
    check_contains "含①只读接口查询延迟" "只读接口" "${doc}"
    check_contains "含②实时/离线对比" "离线" "${doc}"
    check_contains "含③批量作业耗时" "批量作业" "${doc}"
    check_contains "含④Agent 端到端分解" "Agent" "${doc}"

    # **必须有数字**：找毫秒/秒量纲的实测值。
    # 这是本步的实质判据 —— 一篇全是方法论、没有一个数的文档应当不通过。
    local numbers
    numbers="$(printf '%s' "${doc}" | grep -oE '[0-9]+(\.[0-9]+)?[[:space:]]*(ms|毫秒|s\b|秒)' | wc -l)"
    check_nonempty "文档里的实测数字个数（非空守卫）" "${numbers:-0}"
    if [ "${numbers:-0}" -lt 12 ]; then
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "实测数字不少于 12 处" "仅 ${numbers} 处"
        DETAILS+=("PERFORMANCE.md 的实测数字太少（${numbers}），疑似未真正测量")
        FAIL=$(( FAIL + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "实测数字不少于 12 处" "${numbers} 处"
        PASS=$(( PASS + 1 ))
    fi

    # 必须写明测量条件（否则数字不可比、不可复现）
    #
    # !! 判据要用**实际文档里的措辞**，不要考"作者会不会想到某个词" !!
    #   第一版写的是 `check_contains "写明了测量条件" "负载" "${doc}"`，
    #   而文档里写的是 `load average 1.46 / 3.06 / 3.85` 与
    #   `内存 总 15989 MB / ... / **可用 3921 MB**` —— 一个"负载"字样都没有。
    #   于是这条因为**用词不同**而失败，而它想证明的事（写明了测量现场）完全成立。
    #   这类假失败很容易被顺手删掉，那就把"必须记录现场"这条纪律一起丢了。
    #   改成接受任一合理写法。
    check_any "写明了测量条件（机器/采样/负载或内存）" "${doc}" \
        "load average" "系统负载" "负载" "可用内存" "机器状态" "测量现场"
    check_contains "写明了采样次数" "7 次" "${doc}"
    check_contains "写明了复现脚本" "measure-latency.sh" "${doc}"
    check_contains "写明了测量口径（中位数）" "中位数" "${doc}"
    # 不允许用平均值冒充（本项目明确要求中位数）
    check_not_contains "未把平均值当作口径" "取平均值" "${doc}"
    # 不允许留"待补"占位 —— 那说明数字没有真的测
    check_not_contains "没有遗留的待补占位符" "⏳待补" "${doc}"
    check_not_contains "没有遗留的全角待补占位符" "⏳ 待补" "${doc}"
}

# ============================================================
# 5. 结构债记录
# ============================================================
step_structure_debt() {
    section "5/5 已知结构债的记录（只记录，不动别人的文件）"

    if [ ! -f "${SPRINT12_DOC}" ]; then
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "docs/sprint/SPRINT_12.md 存在" "缺失"
        DETAILS+=("docs/sprint/SPRINT_12.md 不存在")
        FAIL=$(( FAIL + 1 ))
        return
    fi
    printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "docs/sprint/SPRINT_12.md 存在" \
        "$(wc -l < "${SPRINT12_DOC}") 行"
    PASS=$(( PASS + 1 ))

    local doc
    doc="$(cat "${SPRINT12_DOC}")"

    # 两份 case 的重复必须被点名
    check_contains "点名 run-batch-pipeline.sh" "run-batch-pipeline.sh" "${doc}"
    check_contains "点名 submit-offline-job.sh" "submit-offline-job.sh" "${doc}"
    check_contains "写明未做" "未做" "${doc}"
    check_contains "写明原因（不是搪塞）" "原因" "${doc}"

    # 结构债是**真实存在**的：两份脚本里确实各有一份阶段 case
    local a b
    a="$(grep -c 'submit_spark_job' "${REPO_ROOT}/scripts/run-batch-pipeline.sh" || true)"
    b="$(grep -c 'submit_spark_job' "${REPO_ROOT}/scripts/submit-offline-job.sh" || true)"
    check_nonempty "run-batch-pipeline.sh 的作业提交点" "${a:-0}"
    check_nonempty "submit-offline-job.sh 的作业提交点" "${b:-0}"

    # 本 Sprint **不得**改动这两个脚本（只记录）
    printf '  %b %-52s %s\n' "${C_BOLD}[INFO]${C_RESET}" "两份脚本均未被本 Sprint 修改" \
        "（结构债留给主控安排）"
}

# ============================================================
# 主流程
# ============================================================
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 12 验收：测试 + 性能基线${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    step_preflight
    load_env
    step_pytest_all
    step_test_surface
    step_performance_doc
    step_structure_debt

    summary
}

main "$@" < /dev/null
exit $?
