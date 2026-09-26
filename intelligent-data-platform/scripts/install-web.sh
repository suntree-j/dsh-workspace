#!/usr/bin/env bash
# ============================================================
# scripts/install-web.sh — 安装数据服务与前端（一次性，幂等）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/install-web.sh
#
# 做什么：
#   1. 安装 Nginx（apt）
#   2. 在项目 venv 中安装数据服务依赖（fastapi / uvicorn）
#   3. 创建专用系统用户 dpapi，并收紧 .env 权限
#   4. 在 Doris 中创建**只读账号** agent_ro（口令写入 .env，不落日志）
#   5. 下载前端运行时（Vue / ECharts）到 services/web/vendor
#   6. 安装 Nginx 站点与 systemd 单元并启动
#   7. 自检：API 健康检查 + 经 Nginx 访问 + 只读账号拒绝写入
#
# 为什么服务层不进 Docker（与数据层的取舍）：
#   数据层（MySQL/Kafka/Doris/Flink/MinIO）继续用 Docker —— 手工装 Doris
#   风险高、且它已经稳定运行、有命名卷持久化。
#   而服务层只有两个进程（uvicorn + nginx），直接用宿主机 systemd + apt 管理，
#   少一层容器网络与端口映射，出问题时 journalctl / nginx -t 就能定位。
#
# 幂等性：所有步骤可重复执行（重复执行只会覆盖配置并重启服务）。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

API_DIR="${REPO_ROOT}/services/api"
WEB_DIR="${REPO_ROOT}/services/web"
VENV_DIR="${REPO_ROOT}/.venv"
API_USER="dpapi"
NGINX_SITE="/etc/nginx/sites-available/data-platform.conf"
SYSTEMD_UNIT="/etc/systemd/system/data-platform-api.service"

# 前端运行时版本（与 services/web/README.md 记录一致）
VUE_VERSION="3.5.13"
ECHARTS_VERSION="5.6.0"

USER_AGENT="data-platform-installer"

log_step() {
    printf '\n'
    printf '%b\n' "${C_BOLD}─────────────────────────────────────────${C_RESET}"
    printf '%b\n' "${C_BOLD}▶ $*${C_RESET}"
    printf '%b\n' "${C_BOLD}─────────────────────────────────────────${C_RESET}"
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "请用 root 执行（需要安装软件包、写 systemd/nginx 配置）"
        exit 1
    fi
}

# ------------------------------------------------------------
# 1. Nginx
# ------------------------------------------------------------
install_nginx() {
    log_step "1/7 安装 Nginx"
    if command -v nginx >/dev/null 2>&1; then
        log_ok "已安装：$(nginx -v 2>&1)"
        return
    fi
    export DEBIAN_FRONTEND=noninteractive
    if apt-get update -qq && apt-get install -y -qq nginx; then
        log_ok "Nginx 安装完成：$(nginx -v 2>&1)"
    else
        log_error "Nginx 安装失败，请检查 apt 源"
        exit 1
    fi
}

# ------------------------------------------------------------
# 2. Python 依赖
# ------------------------------------------------------------
install_deps() {
    log_step "2/7 安装数据服务依赖（venv）"

    if [ ! -x "${VENV_DIR}/bin/python" ]; then
        log_info "创建虚拟环境 ${VENV_DIR}"
        python3 -m venv "${VENV_DIR}"
    fi

    # 用清华镜像加速（服务器实测 0.37s 响应），失败则回退官方源
    local pip_args=(-q --disable-pip-version-check)
    if "${VENV_DIR}/bin/pip" install "${pip_args[@]}" \
            -i https://pypi.tuna.tsinghua.edu.cn/simple \
            -r "${API_DIR}/requirements.txt"; then
        log_ok "依赖安装完成（清华镜像）"
    elif "${VENV_DIR}/bin/pip" install "${pip_args[@]}" -r "${API_DIR}/requirements.txt"; then
        log_ok "依赖安装完成（官方源）"
    else
        log_error "依赖安装失败"
        exit 1
    fi

    "${VENV_DIR}/bin/python" -c "import fastapi, uvicorn, mysql.connector; print('  导入检查通过')"
    # 记录实际安装版本（论文与复现需要具体版本号）
    "${VENV_DIR}/bin/pip" list 2>/dev/null \
        | grep -iE '^(fastapi|uvicorn|mysql-connector-python|pydantic|starlette) ' \
        | sed 's/^/  /' || true
}

# ------------------------------------------------------------
# 3. 运行用户与 .env 权限
# ------------------------------------------------------------
prepare_user() {
    log_step "3/7 创建运行用户并收紧 .env 权限"

    if ! id -u "${API_USER}" >/dev/null 2>&1; then
        useradd --system --no-create-home --shell /usr/sbin/nologin "${API_USER}"
        log_ok "已创建系统用户 ${API_USER}"
    else
        log_ok "系统用户 ${API_USER} 已存在"
    fi

    # .env 里有 Doris 只读口令：只允许 root 与运行用户读取
    chown root:"${API_USER}" "${REPO_ROOT}/.env"
    chmod 640 "${REPO_ROOT}/.env"
    log_ok ".env 权限：$(stat -c '%U:%G %a' "${REPO_ROOT}/.env")"
}

# ------------------------------------------------------------
# 4. Doris 只读账号
#
# 口令处理原则：**不打印、不写进命令行参数**。
#   - 不在命令行里传口令（否则 ps 可见）
#   - SQL 通过 stdin 管道交给 mysql（临时文件 600 权限，用完删除）
# ------------------------------------------------------------
ensure_api_password() {
    local current
    current="$(grep -E '^API_DORIS_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2- || true)"

    if [ -n "${current}" ] && [ "${current}" != "change_me_api_doris" ]; then
        printf '%s' "${current}"
        return
    fi

    # 生成 192 位随机口令（48 位十六进制），只写入 .env，不输出到终端
    local generated
    generated="$(openssl rand -hex 24)"

    if grep -qE '^API_DORIS_PASSWORD=' "${REPO_ROOT}/.env"; then
        sed -i "s|^API_DORIS_PASSWORD=.*|API_DORIS_PASSWORD=${generated}|" "${REPO_ROOT}/.env"
    else
        printf '\n# Sprint 6 数据服务只读账号口令（由 scripts/install-web.sh 生成）\nAPI_DORIS_PASSWORD=%s\n' \
            "${generated}" >> "${REPO_ROOT}/.env"
    fi
    # 其余 API_* 缺省项一并补齐
    add_env_default "API_DORIS_USER" "agent_ro"
    add_env_default "API_DORIS_DATABASE" "ecommerce"
    add_env_default "API_HOST" "127.0.0.1"
    add_env_default "API_PORT" "8000"
    add_env_default "API_ROOT_PATH" "/data/api"
    add_env_default "API_MAX_LIMIT" "1000"
    add_env_default "API_DEFAULT_LIMIT" "60"
    add_env_default "API_QUERY_TIMEOUT" "15"
    add_env_default "API_CONNECT_TIMEOUT" "5"
    chmod 640 "${REPO_ROOT}/.env"

    printf '%s' "${generated}"
}

add_env_default() {
    local key="$1" value="$2"
    grep -qE "^${key}=" "${REPO_ROOT}/.env" || printf '%s=%s\n' "${key}" "${value}" >> "${REPO_ROOT}/.env"
}

create_readonly_account() {
    log_step "4/7 创建 Doris 只读账号（agent_ro）"

    local password
    password="$(ensure_api_password)"
    if [ -z "${password}" ]; then
        log_error "无法确定 API_DORIS_PASSWORD"
        exit 1
    fi

    # 把 SQL 写到 600 权限的临时文件，通过 stdin 交给容器内 mysql
    # （不把口令放进命令行：/proc/<pid>/cmdline 是全局可读的，
    #   而环境变量只有 root 能读，因此下面用 MYSQL_PWD 而不是 -p<口令>）
    local sql_file
    sql_file="$(mktemp)"
    chmod 600 "${sql_file}"

    # Doris 的 DROP USER 不支持 IF EXISTS 时不会致命，因此拆成多条独立执行
    printf "DROP USER 'agent_ro'@'%%';\n" > "${sql_file}"

    local drop_out
    drop_out="$(docker exec -i doris-be mysql -h "${DORIS_FE_IP}" -P 9030 -uroot < "${sql_file}" 2>&1 || true)"
    case "${drop_out}" in
        *ERROR*) log_info "（首次部署无历史账号，忽略 DROP 报错）" ;;
    esac

    {
        printf "CREATE USER 'agent_ro'@'%%' IDENTIFIED BY '%s';\n" "${password}"
        printf "GRANT SELECT_PRIV ON ecommerce.* TO 'agent_ro'@'%%';\n"
        printf "GRANT SELECT_PRIV ON information_schema.* TO 'agent_ro'@'%%';\n"
        # 注意：Doris **不支持** MySQL 的 `FLUSH PRIVILEGES`
        # （实测报 mismatched input 'FLUSH'），而且授权是立即生效的，
        # 因此这里不能加该语句，否则整个 SQL 文件会在最后一行报错。
    } > "${sql_file}"

    local out
    if out="$(docker exec -i doris-be mysql -h "${DORIS_FE_IP}" -P 9030 -uroot < "${sql_file}" 2>&1)"; then
        log_ok "只读账号 agent_ro 已创建并授权（SELECT_PRIV on ecommerce.*）"
    else
        log_error "创建只读账号失败：${out}"
        rm -f "${sql_file}"
        exit 1
    fi
    rm -f "${sql_file}"
}

# ------------------------------------------------------------
# 5. 前端运行时
# ------------------------------------------------------------
fetch_tarball_file() {
    local pkg="$1" version="$2" inner_path="$3" dest="$4"
    local tmp
    tmp="$(mktemp -d)"

    local urls=(
        "https://registry.npmmirror.com/${pkg}/-/${pkg}-${version}.tgz"
        "https://registry.npmjs.org/${pkg}/-/${pkg}-${version}.tgz"
    )
    local url
    for url in "${urls[@]}"; do
        if curl -fsSL --max-time 90 -A "${USER_AGENT}" "${url}" -o "${tmp}/pkg.tgz"; then
            if tar -xzf "${tmp}/pkg.tgz" -C "${tmp}" "package/${inner_path}" 2>/dev/null; then
                install -m 644 "${tmp}/package/${inner_path}" "${dest}"
                rm -rf "${tmp}"
                log_ok "已获取 $(basename "${inner_path}") ← ${url%%/package*}"
                return 0
            fi
        fi
    done
    rm -rf "${tmp}"
    return 1
}

install_frontend_vendor() {
    log_step "5/7 下载前端运行时（Vue / ECharts）"
    mkdir -p "${WEB_DIR}/vendor"

    # 已存在且非空则跳过（避免每次重装都联网）
    if [ -s "${WEB_DIR}/vendor/vue.global.prod.js" ] && [ -s "${WEB_DIR}/vendor/echarts.min.js" ]; then
        log_ok "vendor 已存在，跳过下载"
        return
    fi

    if fetch_tarball_file "vue" "${VUE_VERSION}" "dist/vue.global.prod.js" \
            "${WEB_DIR}/vendor/vue.global.prod.js"; then
        :
    else
        log_error "Vue 运行时下载失败：前端会显示「依赖未就绪」提示，请检查网络后重跑"
    fi

    if fetch_tarball_file "echarts" "${ECHARTS_VERSION}" "dist/echarts.min.js" \
            "${WEB_DIR}/vendor/echarts.min.js"; then
        :
    else
        log_error "ECharts 下载失败：趋势图与排行图将不可用，请检查网络后重跑"
    fi

    ls -lh "${WEB_DIR}/vendor" | tail -n +2
}

# ------------------------------------------------------------
# 6. Nginx 站点 + systemd 单元
# ------------------------------------------------------------
install_nginx_site() {
    log_step "6/7 安装 Nginx 站点与 systemd 单元"

    # 关于 TLS：站点当前是**明文 HTTP**（项目负责人决定暂不做证书，
    # 等注册域名、申请证书、完成备案之后再启用），因此这里不再生成证书。
    # scripts/setup-tls.sh 保留在仓库里，届时直接执行即可；
    # 曾经的"必须先生成证书否则 nginx -t 失败"的约束已随 443 段一起移除
    # （历史实现见 commit 347dfdd）。

    install -m 644 "${REPO_ROOT}/deploy/nginx/data-platform.conf" "${NGINX_SITE}"
    ln -sf "${NGINX_SITE}" /etc/nginx/sites-enabled/data-platform.conf

    # 默认站点同样监听 80 default_server，会造成"duplicate default server"
    if [ -e /etc/nginx/sites-enabled/default ]; then
        rm -f /etc/nginx/sites-enabled/default
        log_info "已停用 Nginx 默认站点（避免 default_server 冲突）"
    fi

    if nginx -t >/tmp/nginx-config-test.log 2>&1; then
        systemctl enable --now nginx >/dev/null 2>&1
        systemctl reload nginx
        log_ok "Nginx 站点已启用"
    else
        log_error "Nginx 配置校验失败："
        cat /tmp/nginx-config-test.log
        exit 1
    fi

    install -m 644 "${REPO_ROOT}/deploy/systemd/data-platform-api.service" "${SYSTEMD_UNIT}"
    systemctl daemon-reload
    systemctl enable data-platform-api >/dev/null 2>&1
    systemctl restart data-platform-api
    sleep 2
    if systemctl is-active --quiet data-platform-api; then
        log_ok "数据服务已启动（systemd: data-platform-api）"
    else
        log_error "数据服务启动失败，排查：journalctl -u data-platform-api -n 50 --no-pager"
        exit 1
    fi
}

# ------------------------------------------------------------
# 7. 自检
# ------------------------------------------------------------
self_check() {
    log_step "7/7 自检"

    local ip
    ip="$(public_ip)"

    if curl -fsS --max-time 10 http://127.0.0.1:8000/health >/dev/null; then
        log_ok "API 直连正常：http://127.0.0.1:8000/health"
    else
        log_error "API 直连失败"
    fi

    local code site
    site="$(site_base)"
    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 10 "${site}/data/api/health")"
    if [ "${code}" = "200" ]; then
        log_ok "经 Nginx 访问正常：${site}/data/api/health"
    else
        log_error "经 Nginx 访问返回 ${code}"
    fi

    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 10 "${site}/data/")"
    if [ "${code}" = "200" ]; then
        log_ok "前端页面可访问：${site}/data/"
    else
        log_error "前端页面返回 ${code}（检查 services/web/index.html 是否存在）"
    fi

    # 只读账号必须写不进去 —— 这是"最小权限"的实证
    local pw
    pw="$(grep -E '^API_DORIS_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    if docker exec -e MYSQL_PWD="${pw}" doris-be mysql -h "${DORIS_FE_IP}" -P 9030 \
            -uagent_ro -e "CREATE TABLE ecommerce.agent_ro_should_fail (id INT);" >/dev/null 2>&1; then
        log_error "只读账号竟然建表成功！请检查授权"
    else
        log_ok "只读账号写入被拒绝（符合预期）"
    fi

    printf '\n'
    printf '%b\n' "${C_GREEN}${C_BOLD} 安装完成${C_RESET}"
    printf '  访问地址： %s://%s/data/\n' "$(site_scheme)" "${ip}"
    printf '  接口文档： %s://%s/data/api/docs\n' "$(site_scheme)" "${ip}"
    printf '  健康检查： bash scripts/verify-sprint-6.sh\n'
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 安装数据服务与前端（Sprint 6）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    require_root
    load_env

    DORIS_FE_IP="${DORIS_FE_IP:-172.28.0.10}"
    export DORIS_FE_IP

    install_nginx
    install_deps
    prepare_user
    create_readonly_account
    install_frontend_vendor
    install_nginx_site
    self_check
}

main "$@" < /dev/null
