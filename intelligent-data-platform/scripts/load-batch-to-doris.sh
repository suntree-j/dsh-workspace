#!/usr/bin/env bash
# ============================================================
# scripts/load-batch-to-doris.sh — 把离线 ADS 结果装载进 Doris（Sprint 3）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/load-batch-to-doris.sh
#
# 做什么：
#   把湖仓里的离线 ADS 结果（Parquet on MinIO）
#   通过 Doris 的 `S3()` 表函数直接读进 Doris 的 `lakehouse_ads` 库，
#   供只读数据服务（Sprint 6 的 FastAPI）查询。
#
# 为什么不用 Stream Load / 外部 loader 进程：
#   Stream Load 要求把文件推到 BE 的 HTTP 端口，就得再起一个"读 S3 → POST"的
#   loader 进程（多一个要维护、要监控、可能挂掉的东西）。
#   Doris 4.x 自带 S3() TVF，可以直接读 S3 兼容存储上的 Parquet：
#     INSERT INTO t SELECT ... FROM S3("uri"="s3://...", "format"="parquet", ...)
#   一条 SQL 解决问题，不引入任何新组件、新端口、新进程。
#   实测（本项目 MinIO）：TVF 读取 ods/orders 的 4 个 part 文件返回 6000 行，
#   与 Spark 侧完全一致；`s3.endpoint` 带不带 http:// 都能用，
#   但 MinIO 必须 `use_path_style = "true"`，否则报 bucket 解析 404。
#
# 幂等：
#   事实表在同一个事务里 TRUNCATE + INSERT；对账汇总表只追加不清空
#   （历史批次本身就是证据链）。
#   Doris 的 DML 支持事务，若 INSERT 中途失败，TRUNCATE 会一起回滚，
#   不会留下"被清空但没有新数据"的窗口 —— 只读服务不会读到空表。
#
# 为什么必须验证行数：
#   装载是"把已经对过账的离线结果搬进服务库"这一步，
#   一旦行数对不上，前面对账的结论就不再适用于服务层看到的数据。
#   因此这里把 Doris 行数与湖仓行数逐一比对，不一致直接失败。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

DORIS_DB="${DORIS_BATCH_DATABASE:-lakehouse_ads}"
DORIS_HOST="${DORIS_FE_HOST:-doris-fe}"
DORIS_QUERY_PORT="${DORIS_FE_QUERY_PORT:-9030}"

# 需要装载的表：表名|S3 目录（相对 bucket）|装载模式|目标列清单
#
# 列清单为什么必须显式写：
#   Parquet 里多了一个分区列 dt，而 Doris 表里没有这一列。
#   用 `SELECT *` 会因为列数不匹配直接失败（报错只提示列数不符，
#   不会告诉你是哪一列）。显式列清单同时让"装载后列顺序"不再依赖巧合。
TABLES=(
    "ads_batch_trade_1m|warehouse/ads/batch_trade_1m/|truncate|window_start, window_end, gmv, order_cnt, order_user_cnt, avg_order_amount, payment_cnt, payment_amount, payment_fail_cnt, payment_success_rate, refund_cnt, refund_amount, refund_rate"
    "ads_batch_trade_1d|warehouse/ads/batch_trade_1d/|truncate|dt, gmv, order_cnt, order_user_cnt, avg_order_amount, payment_cnt, payment_amount, payment_fail_cnt, payment_success_rate, refund_cnt, refund_amount, refund_rate"
    "ads_batch_category_1m|warehouse/ads/batch_category_1m/|truncate|window_start, category_name, window_end, order_cnt, gmv, total_quantity, avg_order_amount"
    "ads_batch_category_1d|warehouse/ads/batch_category_1d/|truncate|dt, category_name, order_cnt, order_user_cnt, gmv, total_quantity, avg_order_amount"
    "ads_reconcile_trade_1m|warehouse/ads/reconcile_trade_1m/|truncate|window_start, realtime_gmv, batch_gmv, diff_gmv, realtime_order_cnt, batch_order_cnt, diff_order_cnt, realtime_order_user_cnt, batch_order_user_cnt, diff_order_user_cnt, realtime_payment_cnt, batch_payment_cnt, diff_payment_cnt, realtime_payment_amount, batch_payment_amount, diff_payment_amount, realtime_payment_fail_cnt, batch_payment_fail_cnt, diff_payment_fail_cnt, realtime_refund_cnt, batch_refund_cnt, diff_refund_cnt, realtime_refund_amount, batch_refund_amount, diff_refund_amount, is_match, compared_at"
    "ads_reconcile_summary|warehouse/ads/reconcile_summary/|append|batch_id, compared_at, scope_start, scope_end, realtime_windows, batch_windows, matched_windows, mismatched_windows, first_mismatch_at, realtime_total_gmv, batch_total_gmv, is_pass"
)

log_stage() { printf '\n%b\n' "${C_BOLD}$*${C_RESET}"; }

# 统一走 stdin 重定向执行 SQL：
#   mysql -e 会把 \$ 当命令（Sprint 1 的 Routine Load 踩过这个坑）；
#   且 heredoc 里的多语句事务只有走 stdin 才会在同一个会话里顺序执行。
# 口令从 .env 注入：容器内没有 .env；doris-fe 里 `-uroot` 不带口令实测被拒绝。
doris_q() {
    local root_pw
    root_pw="$(grep -E '^DORIS_ROOT_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    if [ -z "${root_pw}" ]; then
        log_error ".env 缺少 DORIS_ROOT_PASSWORD"
        exit 1
    fi
    docker exec -i doris-fe mysql -h 127.0.0.1 -P "${DORIS_QUERY_PORT}" \
        -uroot -p"${root_pw}" --connect-timeout=15 "$@" 2>/dev/null
}

doris_sql() { doris_q -B -N; }

# ------------------------------------------------------------
# 0. 前置检查
# ------------------------------------------------------------
precheck() {
    local c
    for c in doris-fe doris-be minio spark-master hive-metastore; do
        if ! container_running "${c}"; then
            log_error "容器 ${c} 未运行"
            exit 1
        fi
    done
    if ! doris_q -e "SELECT 1;" >/dev/null 2>&1; then
        log_error "Doris FE 不可查询（${DORIS_HOST}:${DORIS_QUERY_PORT}）"
        exit 1
    fi
}

# ------------------------------------------------------------
# 湖仓侧行数（用一次性 Spark 容器查，避免依赖 hive 命令行）
# ------------------------------------------------------------
lake_count() {
    local table="$1"
    local out
    out="$(bash "${REPO_ROOT}/scripts/spark-sql.sh" -e \
        "SELECT COUNT(*) FROM lakehouse.${table};" 2>/dev/null \
        | grep -E '^[0-9]+$' | tail -1 || true)"
    printf '%s' "${out:-0}"
}

# ------------------------------------------------------------
# 1. 建库建表（DDL 落盘在 sql/doris/30_batch_ads_tables.sql）
# ------------------------------------------------------------
create_tables() {
    log_stage "1/5 建库建表（${DORIS_DB}）"
    if ! doris_q < "${REPO_ROOT}/sql/doris/30_batch_ads_tables.sql"; then
        log_error "建库建表失败"
        exit 1
    fi
    log_ok "库表就绪"
}

# ------------------------------------------------------------
# 2. 逐表装载（Doris S3() TVF 直读 Parquet）
# ------------------------------------------------------------
load_tables() {
    log_stage "2/5 装载离线指标（Doris S3() TVF 直读 Parquet）"

    local minio_user minio_pw endpoint
    minio_user="$(grep -E '^MINIO_ROOT_USER=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    minio_pw="$(grep -E '^MINIO_ROOT_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    # 容器网络内的 MinIO 地址（Doris 与 MinIO 同网络）
    endpoint="${S3_ENDPOINT_INTERNAL:-http://minio:9000}"

    if [ -z "${minio_user}" ] || [ -z "${minio_pw}" ]; then
        log_error ".env 缺少 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD"
        exit 1
    fi

    local spec table uri mode columns select_list truncate_stmt
    for spec in "${TABLES[@]}"; do
        IFS='|' read -r table uri mode columns <<< "${spec}"
        uri="s3://lakehouse/${uri}"
        printf '  %-26s ← %s  [%s]\n' "${table}" "${uri}" "${mode}"

        # 统一逗号后的空白，避免人工编辑列清单时格式不一致
        select_list="$(printf '%s' "${columns}" | sed 's/[[:space:]]*,[[:space:]]*/, /g')"

        truncate_stmt=""
        if [ "${mode}" = "truncate" ]; then
            truncate_stmt="TRUNCATE TABLE ${DORIS_DB}.${table};"
        fi

        if ! doris_q <<SQL
BEGIN;
${truncate_stmt}
INSERT INTO ${DORIS_DB}.${table} (${select_list})
SELECT ${select_list} FROM S3(
    "uri" = "${uri}*.parquet",
    "format" = "parquet",
    "provider" = "S3",
    "s3.endpoint" = "${endpoint}",
    "s3.region" = "us-east-1",
    "s3.access_key" = "${minio_user}",
    "s3.secret_key" = "${minio_pw}",
    "use_path_style" = "true"
);
COMMIT;
SQL
        then
            log_error "${table} 装载失败（事务已回滚，表内容保持原样）"
            exit 1
        fi
    done
    log_ok "装载完成"
}

# ------------------------------------------------------------
# 3. 行数对账（Doris vs 湖仓）
# ------------------------------------------------------------
verify_counts() {
    log_stage "3/5 行数对账（Doris vs 湖仓）"
    local failed=0 spec table uri mode columns doris_count lake op ok
    for spec in "${TABLES[@]}"; do
        IFS='|' read -r table uri mode columns <<< "${spec}"
        doris_count="$(doris_sql <<< "SELECT COUNT(*) FROM ${DORIS_DB}.${table};" | tail -1)"
        lake="$(lake_count "${table}")"

        # 追加式表（对账汇总）只要求"不少于湖仓"：
        # Doris 侧保留历史批次，行数天然多于单次运行的湖仓结果。
        # 要求"相等"会把正确行为判成失败 —— 那是断言写错，不是数据错。
        ok=0
        op="=="
        if [ "${mode}" = "append" ]; then
            op=">="
            if [ "${doris_count:-0}" -ge "${lake:-0}" ] 2>/dev/null; then ok=1; fi
        else
            if [ "${doris_count}" = "${lake}" ]; then ok=1; fi
        fi

        if [ "${ok}" -eq 1 ]; then
            printf '  %b %-26s Doris %6s 行  %s  湖仓 %6s 行\n' \
                "${C_GREEN}[ OK ]${C_RESET}" "${table}" "${doris_count}" "${op}" "${lake}"
        else
            printf '  %b %-26s Doris %6s 行  %s  湖仓 %6s 行\n' \
                "${C_RED}[FAIL]${C_RESET}" "${table}" "${doris_count}" "${op}" "${lake}"
            failed=$(( failed + 1 ))
        fi
    done
    if [ "${failed}" -gt 0 ]; then
        log_error "${failed} 张表行数校验未通过，装载结果不可信"
        exit 1
    fi
    log_ok "全部 ${#TABLES[@]} 张表行数校验通过"
}

# ------------------------------------------------------------
# 4. 只读账号授权（服务层用 agent_ro 查询这批表）
# ------------------------------------------------------------
grant_readonly() {
    log_stage "4/5 只读账号授权"
    if doris_q -e "GRANT SELECT_PRIV ON ${DORIS_DB}.* TO 'agent_ro'@'%';" >/dev/null 2>&1; then
        log_ok "已授予 agent_ro 对 ${DORIS_DB}.* 的 SELECT_PRIV"
    else
        log_warn "授权语句未生效（可能已授权，或账号不存在）；服务层查询会报 Access denied"
    fi
}

# ------------------------------------------------------------
# 5. 只读账号连通性验证（用真实只读账号查一次）
# ------------------------------------------------------------
verify_readonly() {
    log_stage "5/5 只读账号连通性"
    local ro_pw count gmv
    ro_pw="$(grep -E '^API_DORIS_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    if [ -z "${ro_pw}" ]; then
        log_warn ".env 缺少 API_DORIS_PASSWORD，跳过只读账号验证"
        return 0
    fi

    count="$(docker exec -i doris-fe mysql -h 127.0.0.1 -P "${DORIS_QUERY_PORT}" \
        -uagent_ro -p"${ro_pw}" -B -N 2>/dev/null \
        <<< "SELECT COUNT(*) FROM ${DORIS_DB}.ads_batch_trade_1m;" | tail -1)"

    if [ -z "${count}" ]; then
        log_error "只读账号 agent_ro 无法查询 ${DORIS_DB}（授权或口令有问题）"
        exit 1
    fi
    gmv="$(docker exec -i doris-fe mysql -h 127.0.0.1 -P "${DORIS_QUERY_PORT}" \
        -uagent_ro -p"${ro_pw}" -B -N 2>/dev/null \
        <<< "SELECT SUM(gmv) FROM ${DORIS_DB}.ads_batch_trade_1m;" | tail -1)"

    log_ok "agent_ro 可查询：${count} 个窗口，GMV 合计 ${gmv}"
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 装载离线指标到 Doris（Sprint 3）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    require_docker
    precheck
    create_tables
    load_tables
    verify_counts
    grant_readonly
    verify_readonly

    printf '\n'
    log_ok "完成：离线指标已装载，只读服务可直接查询 ${DORIS_DB}"
}

main "$@" < /dev/null
exit $?
