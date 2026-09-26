#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-4.sh — Sprint 4 验收（Airflow 调度 + 流量域归档）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-4.sh
#
# 验收 8 步：
#   1  服务状态（Airflow 三单元 + 数据服务 + Agent）
#   2  元数据库（用的是 MySQL，不是 sqlite，也不是 PostgreSQL）
#   3  DAG 定义（9 个任务、恢复用 all_done、显式超时、并发限制）
#   4  流量域归档 ODS（行数、分区、与 Kafka offset 对照）
#   5  归档作业自检证据（从最近一次运行日志里核对）
#   6  内存取证（暂停释放量 + 闸门阈值 + 实测记录）
#   7  网关与回归（/airflow/ 可达 + 交易域对账 + Sprint 6/7 未被破坏）
#   8  自动化测试
#
# 为什么"内存取证"单独作为一步：
#   本机可用内存只够"暂停实时链路 → 跑批 → 恢复"这一种执行方式。
#   如果只验功能不验内存约束，将来有人把 DAG 改成直接跑批，
#   功能测试仍然会绿，但机器会被打穿 —— 那正是 Sprint 3 事故的形态。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

PASS=0
FAIL=0
SKIP=0
STEP_NAMES=()
STEP_RESULTS=()
DETAILS=()

step_start() { STEP_NAMES+=("$1"); printf '\n%b\n' "${C_BOLD}$*${C_RESET}"; }
step_record() {
    STEP_RESULTS+=("$2")
    if [ "$2" != "PASS" ] && [ "$2" != "SKIP" ]; then OVERALL=1; fi
}
OVERALL=0

check() {
    local name="$1" expected="$2" actual="$3"
    if [ "${expected}" = "${actual}" ]; then
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${actual}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-46s 期望=[%s] 实际=[%s]\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${expected}" "${actual}"
        DETAILS+=("${name}: 期望 ${expected}，实际 ${actual}")
        FAIL=$(( FAIL + 1 ))
    fi
}

check_true() {
    local name="$1" note="$2"
    shift 2
    if "$@"; then
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${note}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${note}"
        DETAILS+=("${name}: 条件不成立（${note}）")
        FAIL=$(( FAIL + 1 ))
    fi
}

section() { printf '\n%b\n' "${C_BLUE}$*${C_RESET}"; }

# 从 .env 取值（不回显口令）
envv() { grep -E "^$1=" "${REPO_ROOT}/.env" 2>/dev/null | head -1 | cut -d= -f2-; }

# 查湖仓标量的通用入口（用一次性容器，不依赖 Spark 集群状态）
lake_scalar() {
    bash "${REPO_ROOT}/scripts/spark-sql.sh" -e "$1" 2>/dev/null \
        | grep -vE 'WARN|Spark Web UI|Spark master|To adjust|Time taken|^$|^[a-z_]+$' \
        | tail -1 | tr -d '[:space:]'
}

# ------------------------------------------------------------
# 1. 服务状态
# ------------------------------------------------------------
step_services() {
    step_start "1/8 服务状态（Airflow 三单元 + 数据服务 + Agent）"

    local u
    for u in data-platform-airflow-apiserver \
             data-platform-airflow-scheduler \
             data-platform-airflow-dagprocessor \
             data-platform-api \
             data-platform-agent; do
        check "${u} 处于 active" "active" "$(systemctl is-active "${u}" 2>/dev/null)"
    done

    # triggerer 是**有意不部署**的（见 SPRINT_4.md）。它若在跑说明有人加了，
    # 那会白白占一份常驻内存。这里显式确认它不在。
    local trig
    trig="$(systemctl is-active data-platform-airflow-triggerer 2>/dev/null || true)"
    check "triggerer 未部署（有意的取舍，省一份常驻内存）" "inactive" "${trig:-inactive}"
}

# ------------------------------------------------------------
# 2. 元数据库：必须是 MySQL
# ------------------------------------------------------------
step_metadata_db() {
    step_start "2/8 元数据库（MySQL，非 sqlite / 非 PostgreSQL）"

    local conn
    conn="$(bash "${REPO_ROOT}/scripts/airflow.sh" config get-value database sql_alchemy_conn 2>/dev/null | tail -1)"

    case "${conn}" in
        mysql+mysqldb://*) printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "使用 MySQL（mysql+mysqldb）" "ok"; PASS=$(( PASS + 1 )) ;;
        sqlite*) printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "元数据库退回了 sqlite" "${conn%%:*}"; DETAILS+=("元数据库是 sqlite —— 说明 airflow.env 没被加载"); FAIL=$(( FAIL + 1 )) ;;
        *) printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "元数据库不是 MySQL" "${conn%%:*}"; FAIL=$(( FAIL + 1 )) ;;
    esac

    # 未引入 PostgreSQL（AGENTS.md 2.3）
    if printf '%s' "${conn}" | grep -qi 'postgres'; then
        printf '  %b %s\n' "${C_RED}[FAIL]${C_RESET}" "使用了 PostgreSQL —— 违反 AGENTS.md 2.3"
        FAIL=$(( FAIL + 1 ))
    else
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "未引入 PostgreSQL（符合规范）" "ok"
        PASS=$(( PASS + 1 ))
    fi

    # 表数量是"迁移真的建了东西"的实证
    local pw tables
    pw="$(envv AIRFLOW_DB_PASSWORD)"
    tables="$(docker exec -e MYSQL_PWD="${pw}" mysql mysql -uairflow -N -e \
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='airflow';" 2>/dev/null | tr -d '[:space:]')"
    check_true "元数据库已建表（> 20 张）" "实际 ${tables:-0} 张" \
        test "${tables:-0}" -gt 20

    # 最小权限：airflow 账号不该能读业务库
    if docker exec -e MYSQL_PWD="${pw}" mysql mysql -uairflow -N -e \
            "SELECT COUNT(*) FROM ecommerce.orders;" >/dev/null 2>&1; then
        printf '  %b %s\n' "${C_RED}[FAIL]${C_RESET}" "airflow 账号竟能读 ecommerce —— 权限过宽"
        DETAILS+=("airflow 账号权限过宽")
        FAIL=$(( FAIL + 1 ))
    else
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "airflow 账号对 ecommerce 被拒绝（最小权限）" "ok"
        PASS=$(( PASS + 1 ))
    fi
}

# ------------------------------------------------------------
# 3. DAG 定义
# ------------------------------------------------------------
step_dag() {
    step_start "3/8 DAG 定义（9 任务 / all_done 恢复 / 显式超时 / 并发限制）"

    local dag_file="${REPO_ROOT}/airflow/dags/offline_lakehouse_pipeline.py"

    check_true "DAG 文件存在" "${dag_file}" test -f "${dag_file}"

    local tasks
    tasks="$(bash "${REPO_ROOT}/scripts/airflow.sh" tasks list offline_lakehouse_pipeline 2>/dev/null \
             | grep -vE 'graphviz|pip install|You might|^$' | wc -l | tr -d '[:space:]')"
    check "DAG 任务数" "9" "${tasks}"

    check_true "恢复任务用 trigger_rule=all_done（任一上游失败也必须恢复）" "命中 all_done" \
        grep -q 'trigger_rule="all_done"' "${dag_file}"
    check_true "有暂停实时链路的任务" "命中 --pause-only" \
        grep -q 'pause-only' "${dag_file}"
    check_true "并发限制 max_active_runs=1（禁止两次批处理重叠）" "命中" \
        grep -q 'max_active_runs=1' "${dag_file}"
    check_true "含归档任务 archive_behavior" "命中" \
        grep -q 'archive_behavior' "${dag_file}"

    # !! 重试与超时必须显式写在算子上 !!
    #   Airflow 3 的 @dag(default_args=...) 里这两项不生效（实测），
    #   会导致 DAG run 永久卡死。这里断言代码里没有把它们只写在 default_args。
    check_true "重试次数显式写在算子上（不能只依赖 default_args）" "命中 retries=" \
        grep -q 'retries=' "${dag_file}"
    check_true "执行超时显式写在算子上" "命中 execution_timeout=" \
        grep -q 'execution_timeout=' "${dag_file}"

    # 安装自检：归档派发分支与 case 兜底必须都在
    check_true "流水线含 archive 执行分支" "命中 sprint4-archive" \
        grep -q 'sprint4-archive-behavior-events' "${REPO_ROOT}/scripts/run-batch-pipeline.sh"
    check_true "stage 派发有会失败的兜底分支" "命中" \
        grep -q '没有对应的执行分支' "${REPO_ROOT}/scripts/run-batch-pipeline.sh"
}

# ------------------------------------------------------------
# 4. 流量域归档 ODS
# ------------------------------------------------------------
step_archive() {
    step_start "4/8 流量域归档 ODS（行数 / 分区 / 与 Kafka 对照）"

    # 归档行数在第 4 步已经查过一次，这里直接复用，
    # 不再拉起第二次容器 —— 既省时间，也避免末尾那次取值偶发失败
    # 导致输出里出现一个空值（实测遇到过）。
    local rows
    rows="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ods_behavior_event;')"
    check_true "ods_behavior_event 行数 > 0" "实际 ${rows:-0} 行" \
        test "${rows:-0}" -gt 0

    # !! 与 Kafka 的实际消息数对照 —— 这是"零丢失"的硬证据 !!
    local latest
    latest="$(docker exec kafka /opt/kafka/bin/kafka-get-offsets.sh \
                --bootstrap-server kafka:9092 --topic behavior_event --time -1 2>/dev/null \
              | awk -F: '{s+=$3} END {print s+0}')"
    check "归档行数 == Kafka latest offset 合计" "${latest}" "${rows}"

    local days
    days="$(lake_scalar 'SELECT COUNT(DISTINCT dt) FROM lakehouse.ods_behavior_event;')"
    check_true "按 dt 分区（分区数 > 0）" "实际 ${days:-0} 个" \
        test "${days:-0}" -gt 0

    # 漏斗逐级收窄（AGENTS.md 8.2）
    local funnel
    funnel="$(lake_scalar "SELECT CONCAT_WS(',', SUM(CASE WHEN event_type='VIEW' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='CLICK' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='CART' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='BUY' THEN 1 ELSE 0 END)) FROM lakehouse.ods_behavior_event;")"
    local v c ca b
    IFS=',' read -r v c ca b <<< "${funnel}"
    printf '     漏斗： VIEW=%s CLICK=%s CART=%s BUY=%s\n' "${v:-0}" "${c:-0}" "${ca:-0}" "${b:-0}"
    check_true "漏斗逐级收窄 VIEW>CLICK>CART>BUY" "${funnel}" \
        test "${v:-0}" -gt "${c:-0}" -a "${c:-0}" -gt "${ca:-0}" -a "${ca:-0}" -gt "${b:-0}"
}

# ------------------------------------------------------------
# 5. 归档作业自检证据
# ------------------------------------------------------------
step_archive_selfcheck() {
    step_start "5/8 归档作业自检证据（核对最近的运行日志）"

    local log
    log="$(find "${REPO_ROOT}/airflow/logs" -path '*archive*' -name '*.log' 2>/dev/null | sort | tail -1)"
    # DAG 跑过的日志在 airflow/logs，手工跑的在 /tmp
    if [ -z "${log}" ] || ! grep -q '归档自检' "${log}" 2>/dev/null; then
        log="$(grep -l '归档自检' /tmp/archive-verify*.log 2>/dev/null | sort | tail -1)"
    fi

    if [ -z "${log}" ]; then
        printf '  %b %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "找不到归档自检日志（尚未运行过归档阶段）"
        SKIP=$(( SKIP + 1 ))
        return
    fi
    printf '     证据来源： %s\n' "${log}"

    check_true "自检全部通过（命中「项校验全部通过」）" "命中" \
        grep -qE '项校验全部通过|16/16 通过' "${log}"
    check_true "读到了 Kafka 实际消息数" "命中 Kafka 实际消息数" \
        grep -q 'Kafka 实际消息数' "${log}"
    # !! 注意日志里的顺序是「OK 在前、名称在后」!!
    #   Checker 的输出格式是：  [check]   OK   <名称>   <实际值>
    #   最初我把正则写成「名称.*OK」，结果在明明通过的情况下报失败 ——
    #   一个**假阴性**。断言写反比断言缺失更糟：它会让人去查一个不存在的问题。
    check_true "解析行数等于消息数（无解析丢失）" "命中" \
        grep -qE 'OK .*可解析行数 == Kafka 消息数' "${log}"
    check_true "有防空区间守卫（源数据非空）" "命中" \
        grep -q '防空区间假通过' "${log}"
    check_true "归档过程中暂停了实时链路" "命中 已暂停" \
        grep -qE '已暂停 (flink|实时链路已暂停)' "${log}"
    check_true "归档结束后恢复了实时链路" "命中 已恢复" \
        grep -q '实时链路已恢复' "${log}"
}

# ------------------------------------------------------------
# 6. 内存取证
# ------------------------------------------------------------
step_memory() {
    step_start "6/8 内存取证（闸门阈值 / 暂停释放量 / 执行方式）"

    local threshold
    threshold="$(grep -E '^MIN_AVAILABLE_MB_FOR_BATCH=' "${REPO_ROOT}/scripts/lib/memory-guard.sh" \
                 | head -1 | sed 's/.*:-\([0-9]*\).*/\1/')"
    check "批处理内存闸门阈值" "3000" "${threshold:-0}"

    # 闸门必须真的会让批处理失败，而不是打印警告后继续
    check_true "闸门在内存不足时会拒绝（require_memory_for_batch 返回非 0）" "命中" \
        grep -q 'return 1' "${REPO_ROOT}/scripts/lib/memory-guard.sh"

    # 暂停实时链路的释放量：从 .env / 脚本里读容器清单，确认它覆盖 Flink 栈
    check_true "实时链路容器清单含 flink-taskmanager" "命中" \
        grep -q 'REALTIME_STACK_CONTAINERS=.*flink-taskmanager' "${REPO_ROOT}/scripts/lib/memory-guard.sh"

    # DAG 必须是"先暂停再跑批"的结构，否则一定会被闸门拒绝
    check_true "DAG 把暂停放在跑批之前（否则必被闸门拒绝）" "命中" \
        grep -q 'pause >> ods' "${REPO_ROOT}/airflow/dags/offline_lakehouse_pipeline.py"

    printf '     当前宿主机内存： '
    free -m | awk 'NR==2{printf "可用 %s MB / 共 %s MB\n", $7, $2}'
}

# ------------------------------------------------------------
# 7. 网关与回归
# ------------------------------------------------------------
step_gateway_regression() {
    step_start "7/8 网关与回归（/airflow/ 可达 + 交易域对账 + Sprint 6/7 未被破坏）"

    local site code
    site="$(site_base)"

    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 15 "${site}/airflow/" 2>/dev/null || echo 000)"
    check "GET /airflow/ 可达（经 Nginx）" "200" "${code}"

    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 15 "${site}/data/" 2>/dev/null || echo 000)"
    check "GET /data/ 仍正常（Sprint 6 未被破坏）" "200" "${code}"

    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 15 "${site}/data/agent/health" 2>/dev/null || echo 000)"
    check "GET /data/agent/health 仍正常（Sprint 7 未被破坏）" "200" "${code}"

    # 交易域对账结论（Sprint 3 的成果必须还在）
    local rc_sum
    rc_sum="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_trade_1m;')"
    check_true "交易域对账表有数据（Sprint 3 成果仍在）" "实际 ${rc_sum:-0} 行" \
        test "${rc_sum:-0}" -gt 0

    local mismatch
    mismatch="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_trade_1m WHERE is_match = false;')"
    check "交易域对账不一致窗口数" "0" "${mismatch:-0}"
}

# ------------------------------------------------------------
# 8. 自动化测试
# ------------------------------------------------------------
step_tests() {
    step_start "8/8 自动化测试"

    local py=""
    if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
        py="${REPO_ROOT}/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    fi

    if [ -z "${py}" ]; then
        printf '  %b %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "找不到可用的 Python"
        SKIP=$(( SKIP + 1 ))
        return
    fi

    if ( cd "${REPO_ROOT}" && "${py}" -m pytest -m unit -q >/tmp/vs4-unit.log 2>&1 ); then
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "单元测试通过" "$(tail -1 /tmp/vs4-unit.log | tr -d '\n')"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "单元测试失败" "见 /tmp/vs4-unit.log"
        DETAILS+=("单元测试失败")
        FAIL=$(( FAIL + 1 ))
    fi
}

# ------------------------------------------------------------
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 4 验收：Airflow 调度 + 流量域归档${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    cd "${REPO_ROOT}"

    step_services
    step_record "服务状态" "$([ "${FAIL}" -eq 0 ] && echo PASS || echo FAIL)"
    local f1="${FAIL}"

    step_metadata_db
    step_record "元数据库" "$([ "${FAIL}" -eq "${f1}" ] && echo PASS || echo FAIL)"
    local f2="${FAIL}"

    step_dag
    step_record "DAG 定义" "$([ "${FAIL}" -eq "${f2}" ] && echo PASS || echo FAIL)"
    local f3="${FAIL}"

    step_archive
    step_record "流量域归档" "$([ "${FAIL}" -eq "${f3}" ] && echo PASS || echo FAIL)"
    local f4="${FAIL}"

    step_archive_selfcheck
    step_record "归档自检证据" "$([ "${FAIL}" -eq "${f4}" ] && echo PASS || echo FAIL)"
    local f5="${FAIL}"

    step_memory
    step_record "内存取证" "$([ "${FAIL}" -eq "${f5}" ] && echo PASS || echo FAIL)"
    local f6="${FAIL}"

    step_gateway_regression
    step_record "网关与回归" "$([ "${FAIL}" -eq "${f6}" ] && echo PASS || echo FAIL)"
    local f7="${FAIL}"

    step_tests
    step_record "自动化测试" "$([ "${FAIL}" -eq "${f7}" ] && echo PASS || echo FAIL)"

    printf '\n%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 4 验收汇总${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '  通过 %s   失败 %s   跳过 %s\n' "${PASS}" "${FAIL}" "${SKIP}"

    local i
    for i in "${!STEP_NAMES[@]}"; do
        case "${STEP_RESULTS[$i]}" in
            PASS) printf '  %b  %s\n' "${C_GREEN}[PASS]${C_RESET}" "${STEP_NAMES[$i]}" ;;
            SKIP) printf '  %b  %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "${STEP_NAMES[$i]}" ;;
            *)    printf '  %b  %s\n' "${C_RED}[FAIL]${C_RESET}" "${STEP_NAMES[$i]}" ;;
        esac
    done

    if [ "${FAIL}" -gt 0 ]; then
        printf '\n%b\n' "${C_RED}失败明细：${C_RESET}"
        local d
        for d in "${DETAILS[@]}"; do printf '  - %s\n' "${d}"; done
    fi

    printf '\n'
    if [ "${FAIL}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} Sprint 4 验收通过${C_RESET}"
        printf '\n'
        printf '  看板：     %s/data/\n' "$(site_base)"
        printf '  调度 UI：  %s/airflow/\n' "$(site_base)"
        printf '  归档表：   lakehouse.ods_behavior_event（%s 行）\n' \
            "$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ods_behavior_event;')"
    else
        printf '%b\n' "${C_RED}${C_BOLD} Sprint 4 验收未通过，请按上方提示排查${C_RESET}"
        return 1
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
    return 0
}

main "$@" < /dev/null
exit $?
