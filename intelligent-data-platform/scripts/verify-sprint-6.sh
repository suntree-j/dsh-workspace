#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-6.sh — 服务层（数据后台 + 前端）验收
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-6.sh
#
# 验收内容：
#   1. 环境与服务状态（nginx / data-platform-api / docker）
#   2. 前端文件与运行时完整性
#   3. Nginx 配置校验与对外的三条路径
#   4. API 直连健康检查（含"是否使用只读账号"）
#   5. 指标对账：API 的 GMV/订单量 == MySQL 事实源（精确到分）
#   6. 安全验证：只读账号写入被拒绝、写方法被拒绝、行数上限生效
#   7. 自动化测试：单元测试（SQL 守卫）+ 冒烟测试（真实 API）
#
# 全部通过则输出访问地址并 exit 0；任一步失败 exit 1。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

VENV_DIR="${REPO_ROOT}/.venv"
WEB_DIR="${REPO_ROOT}/services/web"

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

http_code() {
    curl_site -s -o /dev/null -w '%{http_code}' --max-time 10 "$1" || echo "000"
}

api_get() {
    curl_site -fsS --max-time 15 "$1" || echo "{}"
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 6 服务层验收${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    local ip
    ip="$(public_ip)"

    # --------------------------------------------------------
    step_start "1/7 服务状态"
    local nginx_state api_state
    nginx_state="$(systemctl is-active nginx 2>/dev/null || echo inactive)"
    api_state="$(systemctl is-active data-platform-api 2>/dev/null || echo inactive)"
    printf '  nginx               : %s\n' "${nginx_state}"
    printf '  data-platform-api   : %s\n' "${api_state}"
    docker ps --format '  {{.Names}} ({{.Status}})' | head -12

    if [ "${nginx_state}" = "active" ] && [ "${api_state}" = "active" ]; then
        step_record "服务状态" "PASS"
    else
        log_error "nginx 或 data-platform-api 未运行；排查： journalctl -u data-platform-api -n 50"
        step_record "服务状态" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "2/7 前端文件与运行时"
    local missing=0 f
    for f in index.html app.js styles.css vendor/vue.global.prod.js vendor/echarts.min.js; do
        if [ -s "${WEB_DIR}/${f}" ]; then
            printf '  [OK]   %-32s %s\n' "${f}" "$(du -h "${WEB_DIR}/${f}" | cut -f1)"
        else
            printf '  %b %-32s 缺失\n' "${C_RED}[FAIL]${C_RESET}" "${f}"
            missing=$(( missing + 1 ))
        fi
    done
    if [ "${missing}" -eq 0 ]; then
        step_record "前端文件" "PASS"
    else
        log_error "缺少 ${missing} 个文件；执行 bash scripts/install-web.sh 补齐运行时"
        step_record "前端文件" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "3/7 Nginx 配置与对外路径"
    if nginx -t >/dev/null 2>&1; then
        log_ok "nginx -t 通过"
        local c_root c_page c_api c_docs c_health
        local site
        site="$(site_base)"
        c_root="$(http_code "${site}/")"
        c_page="$(http_code "${site}/data/")"
        c_api="$(http_code "${site}/data/api/health")"
        c_docs="$(http_code "${site}/data/api/docs")"
        c_health="$(http_code "${site}/data/healthz")"
        printf '  /              → %s（期望 302）\n' "${c_root}"
        printf '  /data/         → %s（期望 200）\n' "${c_page}"
        printf '  /data/api/health → %s（期望 200）\n' "${c_api}"
        printf '  /data/api/docs → %s（期望 200）\n' "${c_docs}"
        printf '  /data/healthz  → %s（期望 200）\n' "${c_health}"
        if [ "${c_root}" = "302" ] && [ "${c_page}" = "200" ] && [ "${c_api}" = "200" ] \
           && [ "${c_docs}" = "200" ]; then
            step_record "Nginx 对外路径" "PASS"
        else
            log_error "对外路径返回码不符合预期"
            step_record "Nginx 对外路径" "FAIL"
        fi
    else
        nginx -t 2>&1 | tail -3
        step_record "Nginx 对外路径" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "4/7 API 健康检查（含只读账号基线）"
    local health
    health="$(api_get "http://127.0.0.1:8000/health")"
    printf '%s\n' "${health}" | head -c 600
    printf '\n'
    if printf '%s' "${health}" | grep -q '"readonly_enforced": *true'; then
        log_ok "使用了专用只读账号（不是 root）"
        step_record "API 健康检查" "PASS"
    else
        log_error "未确认只读账号（readonly_enforced 不为 true）"
        step_record "API 健康检查" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "5/7 指标对账（API ↔ MySQL）"
    local api_gmv api_orders db_gmv db_orders
    api_gmv="$(api_get "http://127.0.0.1:8000/overview" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["kpi"]["gmv"])' 2>/dev/null || echo "")"
    api_orders="$(api_get "http://127.0.0.1:8000/overview" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["kpi"]["order_cnt"])' 2>/dev/null || echo "")"
    db_gmv="$(docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "SELECT SUM(amount) FROM ecommerce.orders"' 2>/dev/null | tr -d '\r')"
    db_orders="$(docker exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "SELECT COUNT(*) FROM ecommerce.orders"' 2>/dev/null | tr -d '\r')"

    printf '  API   GMV=%s  订单量=%s\n' "${api_gmv:-<空>}" "${api_orders:-<空>}"
    printf '  MySQL GMV=%s  订单量=%s\n' "${db_gmv:-<空>}" "${db_orders:-<空>}"

    if [ -n "${api_gmv}" ] && [ "${api_gmv}" = "${db_gmv}" ] && [ "${api_orders}" = "${db_orders}" ]; then
        log_ok "API 与 MySQL 事实源精确一致（含小数位）"
        step_record "指标对账" "PASS"
    else
        log_error "对账不一致"
        step_record "指标对账" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "6/7 安全验证"
    local sec_ok=1

    # 6.1 只读账号不能建表
    local ro_pw ro_out
    ro_pw="$(grep -E '^API_DORIS_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    if docker exec -e MYSQL_PWD="${ro_pw}" doris-be mysql -h "${DORIS_FE_IP:-172.28.0.10}" \
            -P 9030 -uagent_ro -e "CREATE TABLE ecommerce.verify_should_fail (id INT);" \
            >/dev/null 2>&1; then
        log_error "只读账号竟然能建表"
        sec_ok=0
    else
        log_ok "只读账号写入被 Doris 拒绝"
        # 顺带确认账号确实可读
        local ro_rows
        ro_rows="$(docker exec -e MYSQL_PWD="${ro_pw}" doris-be mysql -h "${DORIS_FE_IP:-172.28.0.10}" \
            -P 9030 -uagent_ro -N -B -e "SELECT COUNT(*) FROM ecommerce.ads_realtime_trade_1m;" 2>/dev/null)"
        if [ -n "${ro_rows}" ]; then
            log_ok "只读账号可正常查询（ads_realtime_trade_1m 有 ${ro_rows} 个窗口）"
        else
            log_error "只读账号无法查询业务表"
            sec_ok=0
        fi
    fi

    # 6.2 写方法被拒绝
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X DELETE "http://127.0.0.1:8000/orders")"
    if [ "${code}" = "405" ] || [ "${code}" = "404" ]; then
        log_ok "写方法被拒绝（DELETE /orders → ${code}）"
    else
        log_error "写方法未被拒绝（返回 ${code}）"
        sec_ok=0
    fi

    # 6.3 行数上限生效
    code="$(http_code "http://127.0.0.1:8000/orders?limit=100000")"
    if [ "${code}" = "422" ]; then
        log_ok "超限请求被拒绝（limit=100000 → 422）"
    else
        log_error "超限请求未被拒绝（返回 ${code}）"
        sec_ok=0
    fi

    # 6.4 未知路径不泄露信息
    code="$(http_code "http://127.0.0.1:8000/unknown-endpoint")"
    [ "${code}" = "404" ] && log_ok "未知路径返回 404" || { log_error "未知路径返回 ${code}"; sec_ok=0; }

    if [ "${sec_ok}" -eq 1 ]; then
        step_record "安全验证" "PASS"
    else
        step_record "安全验证" "FAIL"
    fi

    # --------------------------------------------------------
    step_start "7/7 自动化测试"
    local python_bin="${VENV_DIR}/bin/python"
    if [ ! -x "${python_bin}" ]; then
        log_error "找不到 ${python_bin}"
        step_record "自动化测试" "FAIL"
    else
        if ( cd "${REPO_ROOT}" && "${python_bin}" -m pytest -m unit -q 2>&1 | tail -3 ); then
            log_ok "单元测试通过"
        else
            log_error "单元测试失败"
            OVERALL=1
        fi
        if ( cd "${REPO_ROOT}" && "${python_bin}" -m pytest -m smoke -q \
                tests/smoke/test_api.py 2>&1 | tail -5 ); then
            log_ok "服务层冒烟测试通过"
            step_record "自动化测试" "PASS"
        else
            log_error "服务层冒烟测试失败"
            step_record "自动化测试" "FAIL"
        fi
    fi

    # --------------------------------------------------------
    summary "${ip}"
    return "${OVERALL}"
}

summary() {
    local ip="$1"
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
        printf '%b\n' "${C_GREEN}${C_BOLD} Sprint 6 验收通过${C_RESET}"
        printf '\n'
        printf '  数据大屏： %s://%s/data/\n' "$(site_scheme)" "${ip}"
        printf '  接口文档： %s://%s/data/api/docs\n' "$(site_scheme)" "${ip}"
        printf '  健康检查： %s://%s/data/api/health\n' "$(site_scheme)" "${ip}"
    else
        printf '%b\n' "${C_RED}${C_BOLD} Sprint 6 验收未通过，请按上方提示排查${C_RESET}"
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
}

main "$@" < /dev/null
exit $?
