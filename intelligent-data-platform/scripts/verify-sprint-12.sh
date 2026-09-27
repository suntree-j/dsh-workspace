#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-12.sh — Sprint 12 验收（测试 + 性能基线）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-12.sh
#
# 验收项（6 步）：
#   1. 前置可用性   `.env` 存在（缺它就拒跑：一个只读验收不该在坏配置上给结论）
#   2. 测试全量     `python -m pytest` 的**实测通过数**（不是"文件存在"）
#   3. 阶段清单防漂移 两份脚本的阶段清单必须一致（点名例外：load）
#   4. 测试分布     单元/冒烟各自的数量与标记正确性；补的缺口文件确实在
#   5. 性能文档     `docs/PERFORMANCE.md` 存在，且**含实测数字**与四类基线
#   6. 结构债记录   已知结构债在 `SPRINT_12.md` 里写明"未做及原因"
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
    section "1/6 前置可用性"

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
    section "2/6 测试全量（python -m pytest 实测）"

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
    section "4/6 测试分布（单元/冒烟）与本次补齐的缺口"

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
# 3. 阶段清单防漂移（两份脚本不许各自漂移）
# ============================================================
#
# !! 为什么这条断言必须存在（它防的是一类"看起来成功"的缺陷）!!
#
#   `run-batch-pipeline.sh` 与 `submit-offline-job.sh` 各有一份阶段清单：
#     前者 STAGES（全链路顺序） 后者 KNOWN_STAGES（单阶段白名单）+ submit_stage 的 case。
#   两份清单不同步时，症状**不是报错，而是静默空转**：
#   阶段能通过参数校验，却没有执行分支，于是 case 穿透、rc 保持 0，
#   脚本打印「阶段 xxx 完成 / 执行成功」—— 一个什么都没做的阶段报告成功了
#   （Sprint 4 的 archive 阶段真实发生过，Airflow 也据此把它标成 success）。
#
#   本 Sprint（12）的处置是**只加断言、不做合并重构** ——
#   合并两份清单要动离线流水线的入口脚本，收益小于风险（记录见 SPRINT_12.md）。
#   因此这条断言的作用是：把"下次再漂移"从**静默**变成**当场 FAIL 并打印差集**。
#
# !! `load` 阶段的例外必须**显式写在这里并注明原因** !!
#   两份清单**有意不完全相同**：`submit-offline-job.sh` 不含 `load`。
#   原因：`load` 不是 Spark 作业，它走的是 Doris 装载脚本
#   （`scripts/load-batch-to-doris.sh`，S3() TVF 直读 Parquet）。
#   本脚本的单阶段入口只负责"提交一个 Spark 作业"，
#   把一个装载阶段塞进它的 case 只会多一条**行为不同**的分支。
#   所以下面的比较是"剔除 load 之后**必须完全相等**"，
#   而不是"有差异就放过" —— 例外是**点名**的，不是**容忍**的。
step_stage_drift() {
    section "3/6 阶段清单防漂移（run-batch-pipeline.sh ↔ submit-offline-job.sh）"

    local pipeline="${REPO_ROOT}/scripts/run-batch-pipeline.sh"
    local submit="${REPO_ROOT}/scripts/submit-offline-job.sh"

    if [ ! -f "${pipeline}" ] || [ ! -f "${submit}" ]; then
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "两份脚本都存在" \
            "缺失：$([ -f "${pipeline}" ] || printf 'run-batch-pipeline.sh ')$([ -f "${submit}" ] || printf 'submit-offline-job.sh')"
        DETAILS+=("阶段清单无法比对：脚本文件缺失")
        FAIL=$(( FAIL + 1 ))
        return
    fi

    # ---- 解析 STAGES=(...) ----
    # `grep -m2 ... | tail -1`：run-batch-pipeline.sh 里 STAGES 出现了两次
    # （文件头注释区一次、source 之后一次）。取第二次 = 真正生效的那一份。
    # !! 这个坑要记住：若只取第一份，改代码里的清单而没改注释区那一份，
    #    断言会拿**不生效的旧清单**去比对，报出"一致"而实际已经漂移。 !!
    local stages_line known_line
    stages_line="$(grep -m2 '^STAGES=(' "${pipeline}" | tail -1 | sed 's/^STAGES=(//; s/).*//')"
    # 白名单是多行数组：从 `KNOWN_STAGES=(` 那一行起一直收集到**括号配平**为止，
    # 并在 awk 里把 `KNOWN_STAGES=(` 与收尾的 `)` 一起去掉。
    #
    # !! 为什么终止条件必须数括号，而不是"看到 `)` 就停"（实测踩了两次）!!
    #   第 1 版用 `/\)/{exit}` 收尾 —— 结果它**在注释行里提前停下**
    #   （`submit-offline-job.sh` 的注释里就带括号），解析出来的清单只剩几项，
    #   断言报出一大片莫名其妙的差集，看起来像脚本被改坏了。
    #   第 2 版改成只看行首的 `)` —— 又**停不下来**：数组收尾的 `)` 在行尾
    #   （`... iceberg-migrate)`），于是解析器把整个文件都当成清单，
    #   得到 275 个"阶段"。
    #   正确判据是**括号配平**：`depth` 从 0 开始，遇 `(` 加一、遇 `)` 减一，
    #   减到 0 的那一行就是数组结束。这样注释里的括号既不会提前终止，
    #   行尾的 `)` 也能被正确识别。
    #
    # !! 收尾行还要把 `)` 之后的文字去掉 !!
    #   只在开头去掉 `KNOWN_STAGES=(`、留着行尾的 `)`，
    #   解析出来的最后一个阶段会变成 `iceberg-migrate)` ——
    #   于是断言报出"pipeline 独有 iceberg-migrate / submit 独有 iceberg-migrate)"，
    #   看着像别人把脚本改坏了，实际是**解析器自己**多带了一个括号。
    #   这类"假差集"最容易被当成真缺陷。
    known_line="$(awk '
        depth == 0 && !/^KNOWN_STAGES=\(/ { next }
        {
            line = $0
            sub(/^KNOWN_STAGES=\(/, "", line)
            # 数括号：只在**这一行**上数，先数完再决定要不要截断
            opens = 0
            closes = 0
            for (i = 1; i <= length($0); i++) {
                ch = substr($0, i, 1)
                if (ch == "(") { opens++ } else if (ch == ")") { closes++ }
            }
            depth += opens - closes
            if (depth <= 0) { sub(/\).*/, "", line) }
            printf "%s ", line
            if (depth <= 0) { exit }
        }
    ' "${submit}")"
    if [ -z "$(printf '%s' "${known_line}" | tr -d ' \t')" ]; then
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "解析 submit-offline-job.sh 的白名单" "解析为空"
        DETAILS+=("KNOWN_STAGES 解析为空（数组写法变了？解析器需要同步）")
        FAIL=$(( FAIL + 1 ))
        return 1
    fi
    if [ -z "$(printf '%s' "${stages_line}" | tr -d ' \t')" ]; then
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "解析 run-batch-pipeline.sh 的 STAGES" "解析为空"
        DETAILS+=("STAGES 解析为空（数组写法变了？解析器需要同步）")
        FAIL=$(( FAIL + 1 ))
        return 1
    fi

    local stages known
    stages="$(printf '%s\n' ${stages_line} | tr -d ' \t' | grep -v '^$' | sort -u)"
    known="$(printf '%s\n' ${known_line} | tr -d ' \t' | grep -v '^$' | sort -u)"

    printf '     run-batch-pipeline.sh STAGES： %s 项\n' "$(printf '%s\n' "${stages}" | grep -c . || true)"
    printf '     submit-offline-job.sh 白名单： %s 项\n' "$(printf '%s\n' "${known}" | grep -c . || true)"

    check_nonempty "解析到 run-batch-pipeline.sh 的阶段清单（非空守卫）" \
        "$(printf '%s\n' "${stages}" | grep -c . || true)"
    check_nonempty "解析到 submit-offline-job.sh 的阶段清单（非空守卫）" \
        "$(printf '%s\n' "${known}" | grep -c . || true)"

    # ---- 点名例外：load 只在 pipeline 里 ----
    local has_load_pipeline has_load_submit
    has_load_pipeline="$(printf '%s\n' "${stages}" | grep -cx 'load' || true)"
    has_load_submit="$(printf '%s\n' "${known}" | grep -cx 'load' || true)"
    check "load 阶段在 run-batch-pipeline.sh 里存在" "1" "${has_load_pipeline:-0}"
    check "load 阶段在 submit-offline-job.sh 里**有意缺省**（走 Doris 装载脚本）" \
        "0" "${has_load_submit:-0}"

    # ---- 剔除点名例外后，两份清单必须完全相等 ----
    local compared
    compared="$(printf '%s\n' "${stages}" | grep -vx 'load' | grep . || true)"

    local diff_pipe diff_sub
    diff_pipe="$(comm -23 <(printf '%s\n' "${compared}" | grep . | sort) \
                          <(printf '%s\n' "${known}" | grep . | sort) | tr '\n' ' ')"
    diff_sub="$(comm -13 <(printf '%s\n' "${compared}" | grep . | sort) \
                          <(printf '%s\n' "${known}" | grep . | sort) | tr '\n' ' ')"

    check "两份清单在剔除点名例外后完全一致" "" \
        "$([ -z "${diff_pipe}${diff_sub}" ] && echo "" || echo "差集非空")"

    if [ -n "${diff_pipe}${diff_sub}" ]; then
        printf '\n  %b 阶段清单漂移差集（**这就是要看的证据**）：\n' "${C_RED}[FAIL]${C_RESET}"
        printf '     仅 run-batch-pipeline.sh 有（submit-offline-job.sh 跑不了）： %s\n' \
            "${diff_pipe:-（无）}"
        printf '     仅 submit-offline-job.sh 有（全链路会漏掉这一段）： %s\n' \
            "${diff_sub:-（无）}"
        printf '     ! 后果：漏掉的那一段会被**静默跳过**并报告成功（Sprint 4 的 archive 先例）。\n'
        DETAILS+=("阶段清单漂移：pipeline 独有 [${diff_pipe:-无}] / submit 独有 [${diff_sub:-无}]")
        FAIL=$(( FAIL + 1 ))
    fi

    # ---- 反向：STAGES 里每个阶段都必须有 run_stage 的 case 分支 ----
    # （与上面独立：清单可能一致，但两边**同时**漏掉同一个阶段的 case）
    local missing_pipe="" missing_sub="" stage
    while IFS= read -r stage; do
        [ -n "${stage}" ] || continue
        grep -qE "^        ${stage}\)" "${pipeline}" || missing_pipe="${missing_pipe} ${stage}"
    done <<< "${stages}"
    check "STAGES 每一项都有 run_stage 的 case 分支" "" "${missing_pipe}"

    while IFS= read -r stage; do
        [ -n "${stage}" ] || continue
        grep -qE "^        ${stage}\)" "${submit}" || missing_sub="${missing_sub} ${stage}"
    done <<< "${known}"
    check "白名单每一项都有 submit_stage 的 case 分支" "" "${missing_sub}"

    # ---- 兜底分支：未登记阶段必须大声失败，不能静默穿透 ----
    #
    # !! 这里**不能**用 `check_true`（实测踩坑，脚本直接以 127 退出）!!
    #   `check_true` 是 `verify-sprint-5.sh` **自己定义**的辅助函数；
    #   本脚本 source 的 `lib/verify-common.sh` 只提供
    #   check / check_contains / check_any / check_not_contains /
    #   check_in_source / check_nonempty。
    #   调用一个不存在的函数会让 bash 返回 127 —— 在 `set -euo pipefail` 下
    #   整份验收**当场中断**：pytest 已经全绿（381 passed）也照样看不到汇总，
    #   报错只有一行 "command not found"。所以这里改用 check + 显式退出码。
    local rc_fallback=0
    grep -q '没有对应的执行分支' "${pipeline}" || rc_fallback=1
    check "run-batch-pipeline.sh 兜底分支存在（未登记阶段大声失败）" "0" "${rc_fallback}"

    rc_fallback=0
    grep -q '没有对应的执行分支' "${submit}" || rc_fallback=1
    check "submit-offline-job.sh 兜底分支存在（未登记阶段大声失败）" "0" "${rc_fallback}"

    # ---- 阶段描述变量：eval 取不到只会打印空说明（不报错），因此必须显式核对 ----
    local desc_missing=""
    while IFS= read -r stage; do
        [ -n "${stage}" ] || continue
        grep -q "^STAGE_DESC_$(printf '%s' "${stage}" | tr '-' '_')=" "${submit}" \
            || desc_missing="${desc_missing} ${stage}"
    done <<< "${known}"
    check "submit-offline-job.sh 的阶段描述变量齐全" "" "${desc_missing}"

    if [ -z "${diff_pipe}${diff_sub}" ] && [ -z "${missing_pipe}${missing_sub}" ]; then
        printf '  %b %s\n' "${C_BOLD}[INFO]${C_RESET}" \
            "两份清单一致（点名例外：load 只属全链路，走 scripts/load-batch-to-doris.sh）"
    fi
}

# ============================================================
# 4. 性能文档（必须有实测数字）
# ============================================================
step_performance_doc() {
    section "5/6 性能基线文档（必须有实测数字，不能只是方法论）"

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
    section "6/6 已知结构债的记录（只记录，不动别人的文件）"

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

    # 结构债的处置进度（如实标注，不要写成"已经解决"）
    # 第 3 步已经**加了防漂移断言**（把静默漂移变成当场 FAIL + 差集），
    # 但两份清单**仍然存在**（没有合并重构）—— 断言是护栏，不是重构。
    printf '  %b %-52s %s\n' "${C_BOLD}[INFO]${C_RESET}" "两份脚本本身仍未被改（只加了断言）" \
        "防漂移断言见第 3 步；合并重构仍未做（原因见 SPRINT_12.md）"
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
    step_stage_drift
    step_test_surface
    step_performance_doc
    step_structure_debt

    summary
}

# !! 为什么主流程要加"被 source 时不自跑"的判断 !!
#   本脚本里的第 3 步（阶段清单防漂移）是一条**会被单独验证**的断言：
#   要证明它真的会 FAIL，就得故意把两份脚本改到不一致再跑一次。
#   若主流程无条件执行，任何 `source scripts/verify-sprint-12.sh` 的
#   验证壳都会连带跑完整个 pytest 全量 —— 那就没人会去做这个实验了。
#   加上这层判断后，验证壳可以只 `source` 再单独调用 `step_stage_drift`。
#
#   !! 这个判断必须在**调用 main 之前**，不能写成"main 里再判断" !!
#      实测踩过：把 `main "$@"` 无条件放在文件末尾、只在 main 内部加判断，
#      等于没加 —— 因为 `main` → `step_preflight` 在 `.env` 缺失时会
#      **exit 1**，而 `exit` 在 source 进来的脚本里会直接终止调用方的 shell。
#      症状是"实验壳什么都没跑就退出了"，看起来像断言没生效。
#      判断写在这一层，才是真正的"被 source 就只加载、不执行"。
#
#   注意 `"${BASH_SOURCE[0]}" = "$0"` 的语义：直接执行时为真；
#   被 source 时为假（$0 是外层脚本/交互 shell）。这是 bash 的标准写法。
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@" < /dev/null
    exit $?
fi
