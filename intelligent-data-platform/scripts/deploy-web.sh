#!/usr/bin/env bash
# ============================================================
# scripts/deploy-web.sh — 更新数据服务与前端（日常部署）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/deploy-web.sh
#
# 与 install-web.sh 的区别：
#   install-web.sh 是**首次安装**（装 nginx、建账号、下运行时）；
#   deploy-web.sh 只做"让改动生效"：同步配置 → 重启服务 → 自检。
#
# 为什么前端"不需要构建、也不需要拷贝"：
#   前端是纯静态文件（Vue 全局构建 + ECharts，无打包步骤），
#   Nginx 直接以 /opt/data-platform/services/web 为站点根目录，
#   因此代码同步到服务器后，改动已经生效，只需考虑浏览器缓存
#   （index.html 已设 no-cache，vendor 目录按内容长缓存）。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

API_DIR="${REPO_ROOT}/services/api"
WEB_DIR="${REPO_ROOT}/services/web"
VENV_DIR="${REPO_ROOT}/.venv"
SYSTEMD_UNIT="/etc/systemd/system/data-platform-api.service"
NGINX_SITE="/etc/nginx/sites-available/data-platform.conf"

FAILED=0

step() {
    printf '\n%b\n' "${C_BOLD}▶ $*${C_RESET}"
}

check_vendor() {
    step "1/5 检查前端运行时"
    local missing=0
    for f in vue.global.prod.js echarts.min.js; do
        if [ -s "${WEB_DIR}/vendor/${f}" ]; then
            printf '  [OK]   %s (%s)\n' "${f}" "$(du -h "${WEB_DIR}/vendor/${f}" | cut -f1)"
        else
            printf '  %b %s 缺失\n' "${C_RED}[FAIL]${C_RESET}" "${f}"
            missing=1
        fi
    done
    if [ "${missing}" -ne 0 ]; then
        log_warn "执行 bash scripts/install-web.sh 可补齐前端运行时"
        FAILED=1
    fi
}

check_frontend_files() {
    step "2/5 检查前端文件"
    for f in index.html app.js styles.css; do
        if [ -s "${WEB_DIR}/${f}" ]; then
            printf '  [OK]   %s\n' "${f}"
        else
            printf '  %b %s 缺失\n' "${C_RED}[FAIL]${C_RESET}" "${f}"
            FAILED=1
        fi
    done
}

update_python_deps() {
    step "3/5 同步 Python 依赖"
    if "${VENV_DIR}/bin/pip" install -q --disable-pip-version-check \
            -i https://pypi.tuna.tsinghua.edu.cn/simple \
            -r "${API_DIR}/requirements.txt" 2>/dev/null; then
        log_ok "依赖已是最新"
    else
        log_warn "依赖同步失败（继续使用已安装版本）"
    fi
}

reload_services() {
    step "4/5 重新加载配置并重启服务"

    # 证书是 HTTPS 站点的前置条件。缺证书时 `nginx -t` 必然失败，
    # 而那时新配置已经写进 sites-available —— 机器会停在
    # "配置已换、nginx 却起不来"的状态。所以先补齐证书再动配置。
    local tls_ok=1
    if [ ! -s "${TLS_CERT_PATH}" ] || [ ! -s "${TLS_KEY_PATH}" ]; then
        log_warn "未找到 TLS 证书，尝试生成（scripts/setup-tls.sh）"
        if [ "$(id -u)" -eq 0 ] && bash "${REPO_ROOT}/scripts/setup-tls.sh" >/dev/null 2>&1; then
            log_ok "TLS 证书已就绪"
        else
            log_error "证书不可用：请先执行 sudo bash scripts/setup-tls.sh"
            FAILED=1
            tls_ok=0
        fi
    fi

    # Nginx 配置有变化才 reload（避免无意义的中断）
    if [ "${tls_ok}" -eq 0 ]; then
        log_error "跳过 Nginx 配置更新（证书缺失，强行更新会让 nginx 无法重载）"
    elif ! diff -q "${REPO_ROOT}/deploy/nginx/data-platform.conf" "${NGINX_SITE}" >/dev/null 2>&1; then
        # 先备份：`nginx -t` 失败时新配置已经落在磁盘上，
        # 若不还原，机器会停在"文件是坏的、进程还在跑旧的"这种
        # 最尴尬的状态 —— 下一次 nginx 重启就直接起不来。
        cp -a "${NGINX_SITE}" "${NGINX_SITE}.bak" 2>/dev/null || true
        install -m 644 "${REPO_ROOT}/deploy/nginx/data-platform.conf" "${NGINX_SITE}"
        if nginx -t >/tmp/nginx-config-test.log 2>&1; then
            systemctl reload nginx
            rm -f "${NGINX_SITE}.bak"
            log_ok "Nginx 配置已更新并 reload"
        else
            log_error "Nginx 配置校验失败："
            cat /tmp/nginx-config-test.log
            if [ -s "${NGINX_SITE}.bak" ]; then
                mv -f "${NGINX_SITE}.bak" "${NGINX_SITE}"
                log_warn "已还原原配置（nginx 仍按原配置运行，服务未中断）"
            fi
            FAILED=1
        fi
    else
        log_ok "Nginx 配置无变化"
    fi

    if ! diff -q "${REPO_ROOT}/deploy/systemd/data-platform-api.service" "${SYSTEMD_UNIT}" >/dev/null 2>&1; then
        install -m 644 "${REPO_ROOT}/deploy/systemd/data-platform-api.service" "${SYSTEMD_UNIT}"
        systemctl daemon-reload
        log_info "systemd 单元已更新"
    fi

    systemctl restart data-platform-api
    sleep 2
    if systemctl is-active --quiet data-platform-api; then
        log_ok "数据服务已重启"
    else
        log_error "数据服务未起来：journalctl -u data-platform-api -n 50 --no-pager"
        FAILED=1
    fi
}

verify() {
    step "5/5 自检"
    local ip site
    ip="$(public_ip)"
    site="$(site_base)"

    local code
    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 10 "${site}/data/api/health")"
    [ "${code}" = "200" ] && log_ok "API：${site}/data/api/health → 200" \
                          || { log_error "API 返回 ${code}"; FAILED=1; }

    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 10 "${site}/data/")"
    [ "${code}" = "200" ] && log_ok "前端：${site}/data/ → 200" \
                          || { log_error "前端返回 ${code}"; FAILED=1; }

    # HTTP 应当跳转到 HTTPS；否则说明 80 上还挂着旧配置，
    # 用户仍会走到"明文被中间设备改写"那条路上（偶发空 502）。
    if [ "${site}" = "https://127.0.0.1" ]; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1/data/)"
        [ "${code}" = "302" ] && log_ok "HTTP 跳转：http://127.0.0.1/data/ → 302" \
                              || { log_error "HTTP 未跳转（返回 ${code}），请检查证书与 Nginx 配置"; FAILED=1; }
    fi

    printf '\n'
    if [ "${FAILED}" -eq 0 ]; then
        log_ok "对外访问： https://${ip}/data/"
    fi
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 更新数据服务与前端${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    check_vendor
    check_frontend_files
    update_python_deps
    reload_services
    verify

    printf '\n'
    if [ "${FAILED}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} 部署完成${C_RESET}"
        return 0
    fi
    printf '%b\n' "${C_RED}${C_BOLD} 部署存在问题，请按上方提示排查${C_RESET}"
    return 1
}

main "$@" < /dev/null
exit $?
