#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-1.sh — Sprint 1 实时链路验收脚本
# ============================================================
#
# 用法（在仓库根目录）：
#   bash scripts/verify-sprint-1.sh              # 只验收（不动数据）
#   bash scripts/verify-sprint-1.sh --replay     # 重建链路并重放数据后验收
#
# 对应 docs/sprint/SPRINT_1.md 第 6 节的验收标准：
#   1. 环境检查（docker / compose / python）
#   2. docker compose config            校验编排文件
#   3. docker compose up -d             启动实时链路容器
#   4. 等待链路就绪                     8 个 Flink 作业 + 8 个 Routine Load
#   5. bash scripts/health-check.sh     Sprint 0 + Sprint 1 全量健康检查
#   6. python -m pytest -m smoke        冒烟测试（含 tests/smoke/test_realtime.py）
#   7. 数据对账                         MySQL ↔ DWD ↔ ADS 逐层核对
#
# --replay 说明（会清空下游与事件数据，需要人工确认）：
#   停 Flink 作业 → 删 Routine Load → 重建 12 个 Topic（源 + 下游）→
#   清空 8 张 Doris 表 → 重新生成事件 → 重启 Flink 作业。
#   **只影响派生数据与可再生的 Kafka 事件，不触碰 MySQL 业务库。**
#
# 全部通过则输出汇总并 exit 0；任一步失败则 exit 1。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

# ------------------------------------------------------------
# 常量
# ------------------------------------------------------------
DWD_TOPICS="dwd_trade_order_detail dwd_trade_payment_detail \
dwd_trade_refund_detail dwd_traffic_behavior_detail"
METRIC_TOPICS="dws_traffic_overview_1m \
ads_realtime_trade_1m ads_realtime_traffic_1m ads_realtime_category_1m"
DOWNSTREAM_TOPICS="${DWD_TOPICS} ${METRIC_TOPICS}"

# --replay 时**必须连同 4 个源 topic 一起重建**：
#   Flink source 使用 scan.startup.mode = 'earliest-offset'（可重放历史），
#   若只重建下游 topic 而保留源 topic，重新提交的作业会把历史事件再算一遍，
#   窗口指标（GMV 等）会被重复累加成 2 倍。
#   重建源 topic 后重新生成事件，即得到"恰好一代事件"的规范数据集。
SOURCE_TOPICS="order_event payment_event refund_event behavior_event"
REPLAY_TOPICS="${SOURCE_TOPICS} ${DOWNSTREAM_TOPICS}"

DORIS_TABLES="${DOWNSTREAM_TOPICS}"

# 8 个 sink 作业（Flink 作业名 = insert-into_<catalog>.<db>.<sink 表名>）
EXPECTED_SINKS="sink_dwd_trade_order_detail sink_dwd_trade_payment_detail \
sink_dwd_trade_refund_detail sink_dwd_traffic_behavior_detail \
sink_dws_traffic_overview_1m sink_ads_realtime_trade_1m \
sink_ads_realtime_traffic_1m sink_ads_realtime_category_1m"
ROUTINE_LOADS="rl_dwd_trade_order_detail rl_dwd_trade_payment_detail \
rl_dwd_trade_refund_detail rl_dwd_traffic_behavior_detail \
rl_dws_traffic_overview_1m \
rl_ads_realtime_trade_1m rl_ads_realtime_traffic_1m rl_ads_realtime_category_1m"

KAFKA_BIN=/opt/kafka/bin
BOOTSTRAP=kafka:9092

REPLAY=0
[ "${1:-}" = "--replay" ] && REPLAY=1

# ------------------------------------------------------------
# 结果记录
# ------------------------------------------------------------
declare -a STEP_NAMES=()
declare -a STEP_RESULTS=()
OVERALL=0

step_start() {
    printf '\n'
    printf '%b\n' "${C_BOLD}─────────────────────────────────────────${C_RESET}"
    printf '%b\n' "${C_BOLD}▶ $*${C_RESET}"
    printf '%b\n' "${C_BOLD}─────────────────────────────────────────${C_RESET}"
}

step_record() {
    STEP_NAMES+=("$1")
    STEP_RESULTS+=("$2")
    if [ "$2" != "PASS" ] && [ "$2" != "SKIP" ]; then
        OVERALL=1
    fi
}

resolve_python() {
    # 依次尝试：仓库根 .venv（服务器上由部署脚本创建）→ data-generator/.venv
    # → 系统 python3 / python。两个位置都要覆盖 Windows(Scripts) 与 Linux(bin)。
    local candidate
    for candidate in \
        "${REPO_ROOT}/.venv/bin/python" \
        "${REPO_ROOT}/.venv/Scripts/python.exe" \
        "${REPO_ROOT}/data-generator/.venv/bin/python" \
        "${REPO_ROOT}/data-generator/.venv/Scripts/python.exe"; do
        if [ -x "${candidate}" ]; then
            printf '%s' "${candidate}"
            return 0
        fi
    done
    command -v python3 >/dev/null 2>&1 && { printf 'python3'; return 0; }
    command -v python >/dev/null 2>&1 && { printf 'python'; return 0; }
    printf ''
}

# ------------------------------------------------------------
# Doris / Kafka 查询小工具
# ------------------------------------------------------------
doris_sql() {
    docker exec doris-be mysql -h "${DORIS_FE_IP}" -P 9030 -uroot \
        --connect-timeout=10 -N -B -e "$1" 2>/dev/null
}

doris_show() {
    docker exec doris-be mysql -h "${DORIS_FE_IP}" -P 9030 -uroot \
        --connect-timeout=10 -B -e "$1" 2>/dev/null
}

kafka_topic_exists() {
    docker exec kafka "${KAFKA_BIN}/kafka-topics.sh" \
        --bootstrap-server "${BOOTSTRAP}" --list 2>/dev/null | grep -qx "$1"
}

flink_overview() {
    docker exec flink-jobmanager curl -fsS --max-time 8 \
        http://localhost:8081/overview 2>/dev/null
}

# 输出每个作业一行： "<状态> <作业名>"
flink_jobs() {
    docker exec flink-jobmanager curl -fsS --max-time 10 \
        http://localhost:8081/jobs/overview 2>/dev/null \
        | sed 's/},{/}\n{/g' \
        | while IFS= read -r line; do
            local st nm
            st="$(printf '%s' "${line}" | grep -o '"state":"[A-Z_]*"' | head -1 | cut -d'"' -f4)"
            nm="$(printf '%s' "${line}" | grep -o '"name":"[^"]*"'  | head -1 | cut -d'"' -f4)"
            [ -n "${st}" ] && [ -n "${nm}" ] && printf '%s %s\n' "${st}" "${nm}"
        done
}

# 校验：8 个期望的 sink 作业**各恰好一个**处于 RUNNING。
#
# 为什么不能只看 jobs-running 计数（实测踩坑）：
#   SQL Gateway 是 session 模式，重启 flink-jobs 容器并不会取消旧作业，
#   旧作业会继续占 slot 甚至把重放的事件累加到旧窗口状态上（指标翻倍）。
#   只数总数时，「1 个新作业 + 1 个旧作业」和「2 个正常作业」看起来一样。
check_running_jobs_exact() {
    local running missing_names="" dup_names="" sink cnt total=0
    running="$(flink_jobs | awk '$1 == "RUNNING" {print $2}')"

    for sink in ${EXPECTED_SINKS}; do
        cnt="$(printf '%s\n' "${running}" | grep -c -- "${sink}" || true)"
        cnt="${cnt:-0}"
        total=$(( total + cnt ))
        if [ "${cnt}" -eq 0 ]; then
            missing_names="${missing_names} ${sink}"
        elif [ "${cnt}" -gt 1 ]; then
            dup_names="${dup_names} ${sink}×${cnt}"
        fi
    done

    if [ -n "${missing_names}" ]; then
        log_error "缺少运行中的作业:${missing_names}"
        return 1
    fi
    if [ -n "${dup_names}" ]; then
        log_error "存在重复作业（旧 session 遗留）:${dup_names}"
        log_error "请执行： bash scripts/cancel-flink-jobs.sh  后重新提交作业"
        return 1
    fi
    if [ "${total}" -ne 8 ]; then
        log_error "运行中的 sink 作业数为 ${total}，期望 8"
        return 1
    fi
    return 0
}

# ------------------------------------------------------------
# --replay：重建链路并重放数据
# ------------------------------------------------------------
do_replay() {
    step_start "0/7 重建实时链路（--replay）"

    log_info "停止 Flink 作业提交容器 ..."
    compose stop flink-jobs >/dev/null 2>&1 || true

    log_info "取消所有 Flink 作业与 SQL Gateway 会话（防止旧作业占 slot / 指标翻倍） ..."
    bash "${REPO_ROOT}/scripts/cancel-flink-jobs.sh" | tail -4

    log_info "删除 Routine Load 作业 ..."
    local stmts=""
    local j
    for j in ${ROUTINE_LOADS}; do
        stmts="${stmts}STOP ROUTINE LOAD FOR ${j};"
    done
    doris_sql "USE ecommerce; ${stmts}" >/dev/null 2>&1 || true
    sleep 5
    stmts=""
    for j in ${ROUTINE_LOADS}; do
        stmts="${stmts}DROP ROUTINE LOAD FOR ${j};"
    done
    doris_sql "USE ecommerce; ${stmts}" >/dev/null 2>&1 || true

    log_info "重建全部 Topic（源 + 下游，共 12 个） ..."
    local t delete_args=() create_args=()
    # 删除时把所有 topic 一次传给 kafka-topics.sh：
    #   逐个删除时每次都要等 controller 响应，12 个 topic 会花好几分钟。
    for t in ${REPLAY_TOPICS}; do
        delete_args+=(--topic "${t}")
    done
    docker exec kafka "${KAFKA_BIN}/kafka-topics.sh" \
        --bootstrap-server "${BOOTSTRAP}" --delete "${delete_args[@]}" >/dev/null 2>&1 || true
    sleep 15
    for t in ${REPLAY_TOPICS}; do
        docker exec kafka "${KAFKA_BIN}/kafka-topics.sh" \
            --bootstrap-server "${BOOTSTRAP}" --create --topic "${t}" \
            --partitions 3 --replication-factor 1 >/dev/null 2>&1 || true
    done

    log_info "重建 Routine Load（由 doris-be 初始化脚本负责） ..."
    docker exec -e FE_HOST="${DORIS_FE_IP}" -e KAFKA_BOOTSTRAP_SERVERS="${BOOTSTRAP}" \
        doris-be bash -c 'bash /docker-entrypoint-initdb.d/13_routine_load.sh' \
        2>&1 | grep -E "已提交|已存在|完成" | tail -3 || true

    log_info "清空 Doris 下游表（不动 MySQL） ..."
    stmts=""
    for t in ${DORIS_TABLES}; do
        stmts="${stmts}TRUNCATE TABLE ${t};"
    done
    doris_sql "USE ecommerce; ${stmts}" >/dev/null 2>&1 || true

    log_info "重新生成事件 ..."
    compose run --rm -T data-generator python -m src.generate_events 2>&1 | tail -6

    log_info "重启 Flink 作业提交容器 ..."
    compose up -d --force-recreate flink-jobs 2>&1 | tail -1

    step_record "重建链路并重放数据" "PASS"
}

# ------------------------------------------------------------
# 4. 等待链路就绪
# ------------------------------------------------------------
wait_realtime_ready() {
    local timeout="$1"
    local deadline=$(( SECONDS + timeout ))
    local running=0 loads=0

    while [ "${SECONDS}" -lt "${deadline}" ]; do
        local body
        body="$(flink_overview || true)"
        running="$(printf '%s' "${body}" | grep -o '"jobs-running":[0-9]*' | grep -o '[0-9]*$' || true)"
        running="${running:-0}"

        loads="$(doris_show "USE ecommerce; SHOW ROUTINE LOAD\G" \
            | grep -cE '^[[:space:]]*State: RUNNING' || true)"
        loads="${loads:-0}"

        if [ "${running}" -ge 8 ] && [ "${loads}" -ge 8 ] && check_running_jobs_exact >/dev/null 2>&1; then
            return 0
        fi
        printf '\r%b' "${C_BLUE}[INFO]${C_RESET} 等待链路就绪 ... Flink 作业 ${running}/8，Routine Load ${loads}/8 (${SECONDS}s)"
        sleep 10
    done
    printf '\r\033[K'
    log_error "等待超时：Flink 作业 ${running}/8，Routine Load ${loads}/8"
    check_running_jobs_exact || true
    return 1
}

# ------------------------------------------------------------
# 7. 数据对账
# ------------------------------------------------------------
reconcile() {
    local failed=0

    echo "── 各层行数 ──────────────────────────────"
    doris_show "
USE ecommerce;
SELECT 'dwd_trade_order_detail' AS tbl, COUNT(*) AS rows_ FROM dwd_trade_order_detail
UNION ALL SELECT 'dwd_trade_payment_detail', COUNT(*) FROM dwd_trade_payment_detail
UNION ALL SELECT 'dwd_trade_refund_detail', COUNT(*) FROM dwd_trade_refund_detail
UNION ALL SELECT 'dwd_traffic_behavior_detail', COUNT(*) FROM dwd_traffic_behavior_detail
UNION ALL SELECT 'dws_traffic_overview_1m', COUNT(*) FROM dws_traffic_overview_1m
UNION ALL SELECT 'ads_realtime_trade_1m', COUNT(*) FROM ads_realtime_trade_1m
UNION ALL SELECT 'ads_realtime_traffic_1m', COUNT(*) FROM ads_realtime_traffic_1m
UNION ALL SELECT 'ads_realtime_category_1m', COUNT(*) FROM ads_realtime_category_1m;"

    echo
    echo "── MySQL（唯一事实源）────────────────────"
    docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -B -e "
SELECT COUNT(*) AS orders, SUM(amount) AS gmv FROM ecommerce.orders
UNION ALL SELECT COUNT(*), SUM(amount) FROM ecommerce.payment WHERE payment_status=\"SUCCESS\"
UNION ALL SELECT COUNT(*), SUM(refund_amount) FROM ecommerce.refund;"' 2>/dev/null

    echo
    echo "── ADS 指标合计（应与 MySQL 一致）────────"

    local exp_orders exp_gmv exp_pay exp_refund
    exp_orders="$(docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "SELECT COUNT(*) FROM ecommerce.orders"' 2>/dev/null | tr -d '\r')"
    exp_gmv="$(docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "SELECT SUM(amount) FROM ecommerce.orders"' 2>/dev/null | tr -d '\r')"
    exp_pay="$(docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "SELECT COUNT(*) FROM ecommerce.payment WHERE payment_status=\"SUCCESS\""' 2>/dev/null | tr -d '\r')"
    exp_refund="$(docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "SELECT COUNT(*) FROM ecommerce.refund"' 2>/dev/null | tr -d '\r')"

    local got
    got="$(doris_sql "
USE ecommerce;
SELECT CAST(SUM(order_cnt) AS BIGINT), SUM(gmv), CAST(SUM(payment_cnt) AS BIGINT),
       CAST(SUM(refund_cnt) AS BIGINT)
FROM ads_realtime_trade_1m;" | tr '\t' ' ')"

    local g_orders g_gmv g_pay g_refund
    g_orders="$(printf '%s' "${got}" | awk '{print $1}')"
    g_gmv="$(printf '%s' "${got}"    | awk '{print $2}')"
    g_pay="$(printf '%s' "${got}"    | awk '{print $3}')"
    g_refund="$(printf '%s' "${got}" | awk '{print $4}')"

    check_pair() {
        local label="$1" expect="$2" actual="$3"
        if [ "${expect}" = "${actual}" ]; then
            printf '  %b %-28s %s == %s\n' "${C_GREEN}[PASS]${C_RESET}" "${label}" "${actual}" "${expect}"
        else
            printf '  %b %-28s %s != %s\n' "${C_RED}[FAIL]${C_RESET}" "${label}" "${actual}" "${expect}"
            failed=1
        fi
    }

    check_pair "ADS 订单量 vs MySQL"     "${exp_orders}" "${g_orders}"
    check_pair "ADS GMV vs MySQL"        "${exp_gmv}"    "${g_gmv}"
    check_pair "ADS 支付笔数 vs MySQL"   "${exp_pay}"    "${g_pay}"
    check_pair "ADS 退款笔数 vs MySQL"   "${exp_refund}" "${g_refund}"

    echo
    echo "── 类目指标合计 ──────────────────────────"
    local cat
    cat="$(doris_sql "
USE ecommerce;
SELECT COUNT(*), CAST(SUM(order_cnt) AS BIGINT), SUM(gmv) FROM ads_realtime_category_1m;" | tr '\t' ' ')"
    echo "  窗口数 / 订单数 / GMV: ${cat}"
    check_pair "类目订单量 vs MySQL" "${exp_orders}" "$(printf '%s' "${cat}" | awk '{print $2}')"
    check_pair "类目 GMV vs MySQL"   "${exp_gmv}"    "$(printf '%s' "${cat}" | awk '{print $3}')"

    echo
    echo "── 流量指标合计 ──────────────────────────"
    echo "  $(doris_sql "USE ecommerce; SELECT CAST(SUM(pv) AS BIGINT), CAST(SUM(uv) AS BIGINT), CAST(SUM(view_cnt) AS BIGINT), CAST(SUM(buy_cnt) AS BIGINT) FROM ads_realtime_traffic_1m;" | tr '\t' ' ')  (pv uv view buy)"

    echo
    echo "── Routine Load 进度（名称 / 已加载行 / 错误行）──"
    # 行数藏在 Statistic 这个 JSON 字符串里（没有顶层 LoadedRows/ErrorRows 字段）
    doris_show "USE ecommerce; SHOW ROUTINE LOAD\G" | awk '
        /^[[:space:]]*Name:/ { sub(/^[[:space:]]*Name:[[:space:]]*/, ""); name = $0 }
        /^[[:space:]]*Statistic:/ {
            rows = "?"; err = "?"
            if (match($0, /"loadedRows":[0-9]+/)) rows = substr($0, RSTART + 13, RLENGTH - 13)
            if (match($0, /"errorRows":[0-9]+/))  err  = substr($0, RSTART + 12, RLENGTH - 12)
            printf "  %-34s loadedRows=%-8s errorRows=%s\n", name, rows, err
        }'

    return "${failed}"
}

# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 1 实时链路验收${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    require_docker

    DORIS_FE_IP="${DORIS_FE_IP:-172.28.0.10}"
    export DORIS_FE_IP

    if [ "${REPLAY}" -eq 1 ]; then
        do_replay
    fi

    # --------------------------------------------------------
    step_start "1/7 环境检查"
    log_ok "docker 可用：$(docker --version)"
    log_ok "compose 可用：$(docker compose version --short 2>/dev/null || echo 'v2')"
    local python_bin
    python_bin="$(resolve_python)"
    if [ -n "${python_bin}" ]; then
        log_ok "python 可用：${python_bin}"
    else
        log_warn "未找到 python，将跳过测试步骤"
    fi
    step_record "环境检查" "PASS"

    # --------------------------------------------------------
    step_start "2/7 docker compose config（校验编排文件）"
    if compose config --quiet; then
        log_ok "docker compose config 校验通过"
        step_record "docker compose config" "PASS"
    else
        log_error "docker compose config 校验失败"
        step_record "docker compose config" "FAIL"
        summary
        return 1
    fi

    # --------------------------------------------------------
    step_start "3/7 docker compose up -d（启动实时链路）"
    if compose up -d; then
        log_ok "容器已启动"
        step_record "docker compose up -d" "PASS"
    else
        log_error "docker compose up -d 失败"
        step_record "docker compose up -d" "FAIL"
        summary
        return 1
    fi
    compose ps

    # --------------------------------------------------------
    step_start "4/7 等待实时链路就绪（Flink 作业 + Routine Load）"
    log_info "首次启动需要 3~5 分钟（Flink 作业提交 + 窗口触发 + 数据加载）..."
    if wait_realtime_ready 600; then
        log_ok "8 个 Flink 作业运行中，8 个 Routine Load 作业 RUNNING"
        step_record "实时链路就绪" "PASS"
    else
        log_error "排查： docker logs flink-jobs | grep -A2 FAIL"
        log_error "      docker exec doris-be mysql -h ${DORIS_FE_IP} -P 9030 -uroot -e 'USE ecommerce; SHOW ROUTINE LOAD\\G'"
        step_record "实时链路就绪" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "5/7 bash scripts/health-check.sh（健康检查）"
    if bash "${REPO_ROOT}/scripts/health-check.sh"; then
        step_record "health check" "PASS"
    else
        log_error "健康检查未通过"
        step_record "health check" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "6/7 python -m pytest -m smoke（冒烟测试）"
    if [ -z "${python_bin}" ]; then
        log_warn "未找到 python，跳过测试"
        step_record "pytest -m smoke" "SKIP"
    elif ( cd "${REPO_ROOT}" && "${python_bin}" -m pytest -m smoke -q ); then
        log_ok "冒烟测试全部通过"
        step_record "pytest -m smoke" "PASS"
    else
        log_error "冒烟测试未通过"
        step_record "pytest -m smoke" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "7/7 数据对账（MySQL ↔ DWD ↔ ADS）"
    if reconcile; then
        step_record "数据对账" "PASS"
    else
        log_error "对账不一致，链路存在数据问题"
        step_record "数据对账" "FAIL"
    fi

    summary
    return "${OVERALL}"
}

summary() {
    printf '\n'
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 验收汇总${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'

    local i
    for i in "${!STEP_NAMES[@]}"; do
        case "${STEP_RESULTS[$i]}" in
            PASS) printf '  %b  %s\n' "${C_GREEN}[PASS]${C_RESET}" "${STEP_NAMES[$i]}" ;;
            SKIP) printf '  %b  %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "${STEP_NAMES[$i]}" ;;
            *)    printf '  %b  %s\n' "${C_RED}[FAIL]${C_RESET}" "${STEP_NAMES[$i]}" ;;
        esac
    done

    printf '\n'
    if [ "${OVERALL}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} Sprint 1 验收通过${C_RESET}"
        printf '\n'
        printf '下一步：查看实时指标\n'
        printf '  docker exec doris-be mysql -h %s -P 9030 -uroot \\\n' "${DORIS_FE_IP}"
        printf '    -e "USE ecommerce; SELECT * FROM ads_realtime_trade_1m ORDER BY window_start DESC LIMIT 10;"\n'
    else
        printf '%b\n' "${C_RED}${C_BOLD} Sprint 1 验收未通过，请按上方提示排查${C_RESET}"
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
}

main "$@" < /dev/null
exit $?
