#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-2.sh — 离线链路验收（Sprint 2）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-2.sh
#
# 验收内容：
#   1. 服务状态（hive-metastore / spark-master / spark-worker + 既有服务未被拖挂）
#   2. Spark 集群（Worker 已注册、core 与内存可用）
#   3. Hive Metastore 与 ODS 表（5 张表都在 lakehouse 库）
#   4. **逐表行数对账**（湖仓 vs MySQL，精确相等）
#   5. 存储落地（Parquet 真的在 MinIO，不是容器本地盘）
#   6. 类型正确性（金额是 decimal(18,2)，没有 double 漂移风险）
#   7. 幂等（重复执行抽取作业，行数不变）
#   8. 回归（实时链路与数据看板仍健康）
#
# 全部通过 exit 0；任一失败 exit 1。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

declare -a STEP_NAMES=()
declare -a STEP_RESULTS=()
OVERALL=0

# ODS 表 → 对应的 MySQL 表（与 sql/hive/01_ods_tables.sql 一一对应）
#   形如 ods_user:user，冒号左边是湖仓表、右边是业务库表
ODS_PAIRS="ods_user:user ods_product:product ods_orders:orders ods_payment:payment ods_refund:refund"
ODS_TABLES="ods_user ods_product ods_orders ods_payment ods_refund"

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

# spark_sql <SQL>：经由一次性容器执行（见 scripts/spark-sql.sh 的说明）
spark_sql() {
    bash "${REPO_ROOT}/scripts/spark-sql.sh" -e "$1" 2>/dev/null | grep -vE '^(Time taken|[[:space:]]*$)' || true
}

mysql_scalar() {
    docker exec mysql sh -c "mysql -uroot -p\"\$MYSQL_ROOT_PASSWORD\" -N -B -e \"$1\"" 2>/dev/null | tr -d '\r'
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 2 离线链路验收${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    require_docker

    # --------------------------------------------------------
    step_start "1/8 服务状态"
    local unhealthy=0
    for c in hive-metastore spark-master spark-worker; do
        local status
        status="$(docker inspect -f '{{.State.Health.Status}}' "${c}" 2>/dev/null || echo missing)"
        printf '  %-18s %s\n' "${c}" "${status}"
        [ "${status}" != "healthy" ] && unhealthy=$(( unhealthy + 1 ))
    done
    printf '  --- 既有服务（不应被新服务拖挂）---\n'
    docker compose -f "${REPO_ROOT}/docker-compose.yml" --project-directory "${REPO_ROOT}" ps \
        --format '  {{.Name}}  {{.Status}}' 2>/dev/null | head -12

    if [ "${unhealthy}" -eq 0 ]; then
        step_record "服务状态" "PASS"
    else
        log_error "${unhealthy} 个离线链路容器未就绪；排查： docker compose logs --tail=50 hive-metastore spark-master spark-worker"
        step_record "服务状态" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "2/8 Spark 集群"
    # 探测用**服务名**：MasterUI 绑定在容器主机名对应的地址上，
    # 用 localhost 会 Connection refused（实测踩过）
    local master_json
    master_json="$(docker exec spark-master curl -fsS http://spark-master:8080/json/ 2>/dev/null || true)"
    if printf '%s' "${master_json}" | grep -q '"aliveworkers"'; then
        # 两个坑一起避免：
        #   1) Spark 的 /json/ 是美化输出（冒号两侧有空格），必须先 tr 掉空格；
        #   2) 每个取值都要 `|| true`，否则 set -e 下 grep 无匹配会让脚本静默退出。
        local compact workers cores mem
        compact="$(printf '%s' "${master_json}" | tr -d ' \n' || true)"
        workers="$(printf '%s' "${compact}" | grep -o '"aliveworkers":[0-9]*' | cut -d: -f2 || true)"
        cores="$(printf '%s' "${compact}" | grep -o '"cores":[0-9]*' | head -1 | cut -d: -f2 || true)"
        mem="$(printf '%s' "${compact}" | grep -o '"memory":[0-9]*' | head -1 | cut -d: -f2 || true)"
        printf '  Worker=%s  core=%s  可用内存=%s MB\n' "${workers:-?}" "${cores:-?}" "${mem:-?}"
        if [ "${workers:-0}" -ge 1 ]; then
            step_record "Spark 集群" "PASS"
        else
            log_error "没有 Worker 注册"
            step_record "Spark 集群" "FAIL"
        fi
    else
        log_error "Spark Master Web UI(/json) 无响应"
        step_record "Spark 集群" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "3/8 Hive Metastore 与 ODS 表"
    local tables missing=0
    tables="$(spark_sql "SHOW TABLES IN lakehouse;")"
    printf '%s\n' "${tables}" | sed 's/^/  /'
    for t in ${ODS_TABLES}; do
        if ! printf '%s\n' "${tables}" | grep -q "${t}"; then
            log_error "缺少表 lakehouse.${t}"
            missing=$(( missing + 1 ))
        fi
    done
    if [ "${missing}" -eq 0 ]; then
        step_record "ODS 表" "PASS"
    else
        step_record "ODS 表" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "4/8 逐表行数对账（湖仓 vs MySQL）"
    local mismatch=0 pair ods src mysql_count lake_count
    for pair in ${ODS_PAIRS}; do
        ods="${pair%%:*}"
        src="${pair##*:}"
        mysql_count="$(mysql_scalar "SELECT COUNT(*) FROM ecommerce.${src};")"
        lake_count="$(spark_sql "SELECT COUNT(*) FROM lakehouse.${ods};" | head -1 | tr -d '[:space:]')"
        if [ "${mysql_count}" = "${lake_count}" ]; then
            printf '  %b %-12s 湖仓 %-6s == MySQL %-6s\n' "${C_GREEN}[OK]${C_RESET}" "${ods}" "${lake_count}" "${mysql_count}"
        else
            printf '  %b %-12s 湖仓 %-6s != MySQL %-6s\n' "${C_RED}[差异]${C_RESET}" "${ods}" "${lake_count}" "${mysql_count}"
            mismatch=$(( mismatch + 1 ))
        fi
    done
    if [ "${mismatch}" -eq 0 ]; then
        step_record "行数对账" "PASS"
    else
        log_error "${mismatch} 张表行数不一致 —— 离线链路不可信，先查原因（不要调宽断言）"
        step_record "行数对账" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "5/8 存储落地（Parquet 在 MinIO）"
    local mc_out
    mc_out="$(docker exec minio sh -c \
        "MC_HOST_local='http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@localhost:9000'; export MC_HOST_local; mc ls --recursive local/lakehouse/warehouse/ods 2>/dev/null | head -8" || true)"
    printf '%s\n' "${mc_out}" | sed 's/^/  /'
    if printf '%s' "${mc_out}" | grep -q '\.parquet'; then
        local total
        total="$(docker exec minio sh -c \
            "MC_HOST_local='http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@localhost:9000'; export MC_HOST_local; mc ls --recursive local/lakehouse/warehouse/ods 2>/dev/null | wc -l" || echo 0)"
        log_ok "MinIO 中 ods 目录下有 ${total} 个对象"
        step_record "存储落地" "PASS"
    else
        log_error "MinIO 的 lakehouse/warehouse/ods 下没有 Parquet 文件（可能写到了容器本地盘）"
        step_record "存储落地" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "6/8 类型正确性（金额必须是 decimal）"
    local schema double_cols
    schema="$(spark_sql "DESCRIBE lakehouse.ods_orders;")"
    printf '%s\n' "${schema}" | grep -E 'amount|quantity' | sed 's/^/  /' || true
    double_cols="$(printf '%s\n' "${schema}" | grep -cE 'double|float' || true)"
    if [ "${double_cols}" -eq 0 ]; then
        log_ok "ods_orders 中没有 double/float 字段"
        step_record "类型正确性" "PASS"
    else
        log_error "存在 ${double_cols} 个浮点字段，会破坏精确对账"
        step_record "类型正确性" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "7/8 幂等性（重跑抽取作业）"
    log_info "重新提交抽取作业（约 1~3 分钟）..."
    local before after
    before="$(spark_sql "SELECT COUNT(*) FROM lakehouse.ods_orders;" | head -1 | tr -d '[:space:]')"
    if bash "${REPO_ROOT}/scripts/submit-offline-job.sh" >/tmp/sprint2-rerun.log 2>&1; then
        after="$(spark_sql "SELECT COUNT(*) FROM lakehouse.ods_orders;" | head -1 | tr -d '[:space:]')"
        if [ "${before}" = "${after}" ]; then
            log_ok "重跑后 ods_orders 行数仍为 ${after}（幂等）"
            step_record "幂等性" "PASS"
        else
            log_error "重跑后行数变化：${before} → ${after}"
            step_record "幂等性" "FAIL"
        fi
    else
        log_error "重跑失败，详见 /tmp/sprint2-rerun.log"
        tail -20 /tmp/sprint2-rerun.log | sed 's/^/  /'
        step_record "幂等性" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "8/8 回归（实时链路与数据看板）"
    local health_ok=0 api_ok=0
    if bash "${REPO_ROOT}/scripts/health-check.sh" >/tmp/sprint2-health.log 2>&1; then
        health_ok=1
        log_ok "$(grep -c '^\[OK\]' /tmp/sprint2-health.log) 项健康检查全部通过"
    else
        log_error "健康检查未通过："
        grep -E '^\[FAIL\]' /tmp/sprint2-health.log | sed 's/^/  /'
    fi
    if curl -fsS --max-time 10 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        api_ok=1
        log_ok "数据服务 /health 正常"
    else
        log_error "数据服务 /health 无响应"
    fi
    if [ "${health_ok}" -eq 1 ] && [ "${api_ok}" -eq 1 ]; then
        step_record "回归" "PASS"
    else
        step_record "回归" "FAIL"
    fi

    # --------------------------------------------------------
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
        printf '%b\n' "${C_GREEN}${C_BOLD} Sprint 2 验收通过${C_RESET}"
        printf '\n'
        printf '  离线链路已就绪：MySQL → Spark → Parquet(S3A) → Hive 外部表 ods_*\n'
        printf '  下一步（Sprint 3）：在 ods_* 上建 DWD/DWS/ADS，并与实时指标交叉对账\n'
    else
        printf '%b\n' "${C_RED}${C_BOLD} Sprint 2 验收未通过，请按上方提示排查${C_RESET}"
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
}

main "$@" < /dev/null
exit $?
