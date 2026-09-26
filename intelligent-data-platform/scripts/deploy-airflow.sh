#!/usr/bin/env bash
# ============================================================
# scripts/deploy-airflow.sh — 部署 Airflow 调度（Sprint 4）
# ============================================================
#
# 用法（服务器上，仓库根目录，需要 root）：
#   bash scripts/deploy-airflow.sh              # 全量：环境 → 迁移 → 自检
#   bash scripts/deploy-airflow.sh --no-restart # 不重启 systemd 单元
#
# 幂等：重复执行会复用已有 venv / 口令 / 环境文件，只做必要的动作。
#
# !! 为什么 Airflow 装在宿主机而不是 Docker !!
#   决定性理由：Airflow 要调的就是**人手工跑的那同一个脚本入口**
#       bash scripts/run-batch-pipeline.sh --stage ods
#   如果跑在容器里，它要完成同样的事就必须挂 docker.sock（把宿主机
#   容器控制权交给一个 Web 应用）、把 scripts/ 与 Spark 客户端挂进去，
#   还要处理容器内调宿主机 docker 的路径与权限差异。
#   最后一条是根本问题：**调度必须复用人工入口，不能平行实现一套** ——
#   两者行为一旦分叉，就再也说不清"我手工跑是好的，为什么调度跑就错"。
#   这与 Sprint 6 建立的服务层部署原则一致。
#
# !! 为什么必须有独立 venv（.venv-airflow）!!
#   不只是"讲卫生"，而是**硬冲突**，已实测：
#       Airflow 3.3.2 依赖  fastapi>=0.129.0,<0.137.0
#       数据服务已装        fastapi 0.141.1
#   装在一起必然要把线上看板的接口依赖降级。分开是唯一正确的做法。
#
# !! 元数据库驱动：必须是 mysqlclient（一次错判的完整记录）!!
#   我一开始的推理是："元数据库用哪个驱动完全由 SQLAlchemy 连接串决定，
#   所以装核心包 + PyMySQL 就能避开 apache-airflow[mysql] 带来的
#   mysqlclient 编译问题"。**这个推理是错的。**
#
#   实测报错：
#       RuntimeError: You do not have `mysqlclient` package installed.
#   去读源码才看清 airflow/settings.py 的 configure_adapters()：
#       if SQL_ALCHEMY_CONN.startswith("mysql"):
#           try:
#               import MySQLdb.converters
#           except ImportError:
#               raise RuntimeError("You do not have `mysqlclient` ...")
#   即：**只要连接串以 mysql 开头，核心包就硬性要求 MySQLdb**，
#   无论你打算用哪个驱动，也没有配置开关。
#
#   所以正确的做法不是绕，而是把编译依赖备齐：
#   pkg-config + build-essential + python3-dev + default-libmysqlclient-dev，
#   并且**实测确认拿到的是 MySQL 官方客户端库**（而非 MariaDB 的）——
#   因为 MariaDB 客户端库与 MySQL 8 的 caching_sha2_password 有过兼容问题：
#       mysql_config --version → 8.0.46
#       mysql_config --libs    → -lmysqlclient -lz -lzstd -lssl -lcrypto
#   是 Oracle 真库，认证一次通过。
#
#   连接串因此用官方支持的 mysql+mysqldb://，而不是 mysql+pymysql://。
#   PyMySQL 已从 venv 卸载：在 MySQLdb 成为硬要求之后，它不再有任何作用，
#   留着只会是一个"看起来有用其实没用"的依赖。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

AIRFLOW_HOME_DIR="${REPO_ROOT}/airflow"
VENV_DIR="${REPO_ROOT}/.venv-airflow"
ENV_FILE="${REPO_ROOT}/.env"
AIRFLOW_ENV_FILE="${AIRFLOW_HOME_DIR}/airflow.env"
AIRFLOW_USER="dpaiflow"
AIRFLOW_GROUP="dpapi"
API_PORT="${AIRFLOW_API_PORT:-8085}"

UNIT_DIR="/etc/systemd/system"
UNIT_PREFIX="data-platform-airflow"
NO_RESTART=0
FAILED=0

usage() {
    cat <<'TXT'
用法： bash scripts/deploy-airflow.sh [选项]

选项：
  --no-restart   只更新配置与迁移，不重启 systemd 单元
  -h, --help     显示本帮助
TXT
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --no-restart) NO_RESTART=1; shift ;;
            -h|--help) usage; exit 0 ;;
            *) log_error "未知参数：$1"; usage; exit 2 ;;
        esac
    done
}

step() { printf '\n%b\n' "${C_BOLD}▶ $*${C_RESET}"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "需要 root： sudo bash scripts/deploy-airflow.sh"
        exit 1
    fi
}

# ------------------------------------------------------------
# 1. 前置检查
# ------------------------------------------------------------
preflight() {
    step "1/6 前置检查"

    if [ ! -x "${VENV_DIR}/bin/airflow" ]; then
        log_error "找不到 ${VENV_DIR}/bin/airflow"
        log_error "请先安装 Airflow 3.3.2（独立 venv），再执行本脚本"
        exit 1
    fi
    log_ok "Airflow venv 就绪： $("${VENV_DIR}/bin/airflow" version 2>/dev/null | head -1)"

    if [ -z "$(grep -E '^AIRFLOW_DB_PASSWORD=' "${ENV_FILE}" 2>/dev/null || true)" ]; then
        log_error ".env 中缺少 AIRFLOW_DB_PASSWORD"
        log_error "请先执行： bash scripts/setup-airflow-db.sh"
        exit 1
    fi
    log_ok "元数据库配置已存在"

    # 元数据库必须能连上（Airflow 迁移失败时错误信息很难读，先自己查）
    if ! docker exec -e MYSQL_PWD="${MYSQL_ROOT_PASSWORD:-}" mysql \
            mysql -uroot -N -e "SELECT 1;" >/dev/null 2>&1; then
        log_warn "MySQL 容器可能未就绪，迁移有失败风险"
    fi
}

# ------------------------------------------------------------
# 2. 系统用户
#
# !! 为什么这个用户必须在 docker 组里（这是有意的、也是必须说明的取舍）!!
#   Airflow 的任务要暂停/恢复 Flink 容器（内存闸门要求错峰跑批），
#   而控制 docker 就必须有 docker 组权限。**docker 组等价于 root**，
#   所以这不是"加固"，而是"为了让调度能干活必须给的权限"。
#   如实记录，不假装它是最小权限。
#
#   组 dpapi 是为了读 /opt/data-platform/.env（640 root:dpapi）——
#   DAG 里的任务要跑 scripts/*.sh，那些脚本会 source lib/common.sh
#   并 load_env，因此任务身份必须能读到 .env。
# ------------------------------------------------------------
setup_user() {
    step "2/6 系统用户与目录"

    if ! id -u "${AIRFLOW_USER}" >/dev/null 2>&1; then
        useradd --system --create-home --shell /usr/sbin/nologin "${AIRFLOW_USER}"
        log_ok "已创建系统用户 ${AIRFLOW_USER}"
    else
        log_ok "系统用户 ${AIRFLOW_USER} 已存在"
    fi

    usermod -aG "${AIRFLOW_GROUP}" "${AIRFLOW_USER}" 2>/dev/null || true
    usermod -aG docker "${AIRFLOW_USER}" 2>/dev/null || true

    local groups_now
    groups_now="$(id -nG "${AIRFLOW_USER}" 2>/dev/null)"
    log_info "所属组： ${groups_now}"
    case "${groups_now}" in
        *docker*) log_ok "可控制容器（跑批需要暂停/恢复 Flink）" ;;
        *) log_warn "不在 docker 组，DAG 将无法暂停实时链路" ;;
    esac

    install -d -m 755 -o "${AIRFLOW_USER}" -g "${AIRFLOW_GROUP}" "${AIRFLOW_HOME_DIR}"
    install -d -m 755 -o "${AIRFLOW_USER}" -g "${AIRFLOW_GROUP}" "${AIRFLOW_HOME_DIR}/dags"
    install -d -m 755 -o "${AIRFLOW_USER}" -g "${AIRFLOW_GROUP}" "${AIRFLOW_HOME_DIR}/logs"
    install -d -m 755 -o "${AIRFLOW_USER}" -g "${AIRFLOW_GROUP}" "${AIRFLOW_HOME_DIR}/plugins"
    log_ok "目录就绪： ${AIRFLOW_HOME_DIR}/{dags,logs,plugins}"
}

# ------------------------------------------------------------
# 3. 生成 Airflow 环境文件
#
# !! 为什么不把配置写进 systemd 单元 !!
#   单元文件是要进 Git 的（deploy/systemd/*.service），
#   而 SQLALCHEMY_CONN 里含口令 —— 写进去就等于提交密钥。
#   所以单元只引用 EnvironmentFile 的**路径**，
#   真正的凭据落在 600 的 airflow.env 里（不进 Git）。
# ------------------------------------------------------------
gen_env_file() {
    step "3/6 生成 Airflow 环境文件"

    local fernet api_secret jwt_secret
    fernet="$(grep -E '^AIRFLOW_FERNET_KEY=' "${ENV_FILE}" | head -1 | cut -d= -f2- || true)"
    if [ -z "${fernet}" ]; then
        fernet="$("${VENV_DIR}/bin/python" -c \
            'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
        printf '\n# ---------- Sprint 4: Airflow 密钥（自动生成，勿手改）----------\n' >> "${ENV_FILE}"
        printf 'AIRFLOW_FERNET_KEY=%s\n' "${fernet}" >> "${ENV_FILE}"
        api_secret="$(openssl rand -hex 32)"
        printf 'AIRFLOW_API_SECRET_KEY=%s\n' "${api_secret}" >> "${ENV_FILE}"
        jwt_secret="$(openssl rand -hex 32)"
        printf 'AIRFLOW_JWT_SECRET=%s\n' "${jwt_secret}" >> "${ENV_FILE}"
        log_ok "已生成 FERNET_KEY / API_SECRET_KEY / JWT_SECRET 并写入 .env"
    else
        api_secret="$(grep -E '^AIRFLOW_API_SECRET_KEY=' "${ENV_FILE}" | head -1 | cut -d= -f2- || true)"
        jwt_secret="$(grep -E '^AIRFLOW_JWT_SECRET=' "${ENV_FILE}" | head -1 | cut -d= -f2- || true)"
        log_ok "复用 .env 中已有的密钥"
    fi

    # 公网访问地址：反代挂在 /airflow 子路径下，UI 生成的链接必须带这个前缀
    local base_url
    base_url="$(site_scheme)://$(public_ip)/airflow"

    # 用 umask 保证文件一创建就是 600（避免"先 644 后 chmod"的窗口期）
    ( umask 077; : > "${AIRFLOW_ENV_FILE}" )

    {
        printf '# 本文件由 scripts/deploy-airflow.sh 生成，请勿手工编辑。\n'
        printf '# 为什么单独一个文件：systemd 单元要进 Git，而这里含口令。\n'
        printf 'AIRFLOW_HOME=%s\n' "${AIRFLOW_HOME_DIR}"
        printf 'AIRFLOW__CORE__DAGS_FOLDER=%s/dags\n' "${AIRFLOW_HOME_DIR}"
        printf 'AIRFLOW__CORE__PLUGINS_FOLDER=%s/plugins\n' "${AIRFLOW_HOME_DIR}"
        printf 'AIRFLOW__CORE__LOAD_EXAMPLES=False\n'
        printf 'AIRFLOW__CORE__EXECUTOR=LocalExecutor\n'
        printf 'AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Shanghai\n'
        printf 'AIRFLOW__CORE__FERNET_KEY=%s\n' "${fernet}"
        # 并发：4 核 / 可用内存仅 3 GB，宁可慢也不要并发打穿内存
        printf 'AIRFLOW__CORE__PARALLELISM=4\n'
        printf 'AIRFLOW__CORE__MAX_ACTIVE_TASKS_PER_DAG=2\n'
        # 最关键的一条：绝不允许两次批处理重叠，重叠必然打穿内存
        printf 'AIRFLOW__CORE__MAX_ACTIVE_RUNS_PER_DAG=1\n'
        printf 'AIRFLOW__CORE__AUTH_MANAGER=airflow.api_fastapi.auth.managers.simple.simple_auth_manager.SimpleAuthManager\n'
        printf 'AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS=admin:ADMIN\n'
        printf 'AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_PASSWORDS_FILE=%s/simple_auth_passwords.json\n' "${AIRFLOW_HOME_DIR}"
        # !! 这一项必须是 False !!
        #   True 时 Airflow 允许"不带任何凭据"直接签发管理员 token，
        #   等于把能控制容器的调度器完全敞开。
        printf 'AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=False\n'
        printf 'AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=mysql+mysqldb://%s:%s@%s:%s/%s?charset=utf8mb4\n' \
            "$(grep -E '^AIRFLOW_DB_USER=' "${ENV_FILE}" | head -1 | cut -d= -f2-)" \
            "$(grep -E '^AIRFLOW_DB_PASSWORD=' "${ENV_FILE}" | head -1 | cut -d= -f2-)" \
            "$(grep -E '^AIRFLOW_DB_HOST=' "${ENV_FILE}" | head -1 | cut -d= -f2-)" \
            "$(grep -E '^AIRFLOW_DB_PORT=' "${ENV_FILE}" | head -1 | cut -d= -f2-)" \
            "$(grep -E '^AIRFLOW_DB_NAME=' "${ENV_FILE}" | head -1 | cut -d= -f2-)"
        printf 'AIRFLOW__API__HOST=127.0.0.1\n'
        printf 'AIRFLOW__API__PORT=%s\n' "${API_PORT}"
        printf 'AIRFLOW__API__WORKERS=1\n'
        printf 'AIRFLOW__API__BASE_URL=%s\n' "${base_url}"
        printf 'AIRFLOW__API__SECRET_KEY=%s\n' "${api_secret}"
        printf 'AIRFLOW__API_AUTH__JWT_SECRET=%s\n' "${jwt_secret}"
        printf 'AIRFLOW__LOGGING__BASE_LOG_FOLDER=%s/logs\n' "${AIRFLOW_HOME_DIR}"
        printf 'AIRFLOW__LOGGING__LOGGING_LEVEL=INFO\n'
        printf 'AIRFLOW__SCHEDULER__MAX_TIS_PER_QUERY=16\n'
        printf 'AIRFLOW__SCHEDULER__MIN_FILE_PROCESS_INTERVAL=60\n'
        # DAG 会调用仓库里的脚本，脚本要靠 .env 找路径与口令
        printf 'DATA_PLATFORM_REPO=%s\n' "${REPO_ROOT}"
    } > "${AIRFLOW_ENV_FILE}"

    chown "${AIRFLOW_USER}:${AIRFLOW_GROUP}" "${AIRFLOW_ENV_FILE}"
    chmod 640 "${AIRFLOW_ENV_FILE}"
    log_ok "已生成 ${AIRFLOW_ENV_FILE}（640 ${AIRFLOW_USER}:${AIRFLOW_GROUP}）"
    log_info "对外地址将使用： ${base_url}"

    # 口令绝不回显：只打印脱敏后的连接串
    local shown
    shown="$(sed -n 's|^AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=mysql+mysqldb://[^:]*:[^@]*@|AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=mysql+mysqldb://<user>:<redacted>@|p' \
        "${AIRFLOW_ENV_FILE}")"
    printf '  %s\n' "${shown}"
}

# ------------------------------------------------------------
# 4. 数据库迁移
# ------------------------------------------------------------
migrate_db() {
    step "4/6 元数据库迁移"

    set -a
    # shellcheck disable=SC1090
    . "${AIRFLOW_ENV_FILE}"
    set +a

    if "${VENV_DIR}/bin/airflow" db migrate 2>&1 | tail -5; then
        log_ok "db migrate 完成"
    else
        log_error "db migrate 失败"
        FAILED=1
        return 1
    fi

    if "${VENV_DIR}/bin/airflow" db check >/dev/null 2>&1; then
        log_ok "db check 通过"
    else
        log_error "db check 失败"
        FAILED=1
    fi

    # 表数量是"迁移真的建了东西"的实证，而不是"命令没报错"
    local tables
    tables="$(docker exec -e MYSQL_PWD="${AIRFLOW_DB_PASSWORD:-}" mysql \
        mysql -u"${AIRFLOW_DB_USER:-airflow}" -N -e \
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='airflow';" 2>/dev/null || echo 0)"
    if [ "${tables:-0}" -gt 20 ]; then
        log_ok "元数据库已建表： ${tables} 张"
    else
        log_error "元数据库表数量异常： ${tables}"
        FAILED=1
    fi

    # 取消迁移期间可能被拉起的示例 DAG
    log_info "示例 DAG： $("${VENV_DIR}/bin/airflow" config get-value core load_examples 2>/dev/null | head -1)"
}

# ------------------------------------------------------------
# 5. 安装 systemd 单元
#
# 三个单元，而不是一个：
#   apiserver     Web UI + REST API
#   scheduler     解析 DAG、调度任务（权限最大的一个，DAG 任务由它 fork）
#   dagprocessor  独立解析 DAG（Airflow 3 新增；2.x 里是 scheduler 的活）
#
# **不部署 triggerer**：它只为 deferrable operator 服务，本项目全是短时
# shell / Spark 任务，用不到。在可用内存仅约 3 GB 的机器上，
# 少一个常驻进程是实打实的收益 —— 这是有意的取舍，不是漏装。
# ------------------------------------------------------------
install_units() {
    step "5/6 安装 systemd 单元"

    local f name changed=0
    for f in "${REPO_ROOT}"/deploy/systemd/data-platform-airflow-*.service; do
        [ -f "${f}" ] || continue
        name="$(basename "${f}")"
        if ! diff -q "${f}" "${UNIT_DIR}/${name}" >/dev/null 2>&1; then
            install -m 644 "${f}" "${UNIT_DIR}/${name}"
            log_ok "已更新 ${name}"
            changed=1
        else
            log_ok "${name} 无变化"
        fi
    done

    systemctl daemon-reload
    if [ "${changed}" -eq 1 ]; then
        log_info "已 daemon-reload"
    fi
}

# ------------------------------------------------------------
# 6. 启动与自检
# ------------------------------------------------------------
start_services() {
    step "6/6 启动与自检"

    local units=(apiserver scheduler dagprocessor)
    local u

    for u in "${units[@]}"; do
        systemctl enable "${UNIT_PREFIX}-${u}" >/dev/null 2>&1 || true
    done

    if [ "${NO_RESTART}" -eq 1 ]; then
        log_info "--no-restart：只安装不启动"
        return 0
    fi

    for u in "${units[@]}"; do
        systemctl restart "${UNIT_PREFIX}-${u}"
    done

    # 给 api-server 一点启动时间（它要连元数据库并加载 FastAPI 应用）
    sleep 15

    for u in "${units[@]}"; do
        if systemctl is-active --quiet "${UNIT_PREFIX}-${u}"; then
            local rss
            rss="$(systemctl show -p MemoryCurrent --value "${UNIT_PREFIX}-${u}" 2>/dev/null || echo "")"
            if [ -n "${rss}" ] && [ "${rss}" != "[not set]" ]; then
                log_ok "${UNIT_PREFIX}-${u} 已启动（内存 $((rss / 1048576)) MB）"
            else
                log_ok "${UNIT_PREFIX}-${u} 已启动"
            fi
        else
            log_error "${UNIT_PREFIX}-${u} 未起来"
            log_error "  排查： journalctl -u ${UNIT_PREFIX}-${u} -n 40 --no-pager"
            FAILED=1
        fi
    done

    # 端口自检：确认 API server 真的在监听，而不是"进程在但没服务"
    local codes=""
    local path
    for path in "/" "/health" "/api/v2/monitor/health"; do
        local c
        c="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
            "http://127.0.0.1:${API_PORT}${path}" 2>/dev/null || echo 000)"
        printf '  %-26s → %s\n' "${path}" "${c}"
        codes="${codes} ${c}"
    done

    case "${codes}" in
        *200*|*302*|*307*) log_ok "API server 在 127.0.0.1:${API_PORT} 响应" ;;
        *) log_error "API server 无响应（全部 ${codes}）"; FAILED=1 ;;
    esac

    # !! 口令文件权限必须收紧 !!
    #   api-server 首次启动时会生成 simple_auth_passwords.json，
    #   用的是默认 umask → **644，全局可读**，而里面是**明文口令**。
    #   这是实测发现的（ls -l 显示 -rw-r--r--），不是假想问题。
    #   每次部署都 chmod 一次，保证即使文件被重建也不会留着宽权限。
    local pf="${AIRFLOW_HOME_DIR}/simple_auth_passwords.json"
    if [ -f "${pf}" ]; then
        chmod 600 "${pf}"
        chown "${AIRFLOW_USER}:${AIRFLOW_GROUP}" "${pf}" 2>/dev/null || true
        log_ok "登录口令文件： $(stat -c '%a %U:%G' "${pf}")"
        printf '    查看口令（仅 root）： cat %s\n' "${pf}"
    else
        log_warn "尚未生成登录口令文件（api-server 首次启动时创建）"
    fi
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 部署 Airflow 调度（Sprint 4）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    parse_args "$@"
    require_root
    load_env

    preflight
    setup_user
    gen_env_file
    migrate_db
    install_units
    start_services

    printf '\n'
    if [ "${FAILED}" -eq 0 ]; then
        log_ok "Airflow 部署完成"
        printf '  Web UI（本机）： http://127.0.0.1:%s/\n' "${API_PORT}"
        printf '  Web UI（对外）： %s://%s/airflow/   （需先配置 Nginx 反代）\n' \
            "$(site_scheme)" "$(public_ip)"
        printf '  登录口令文件：   %s/simple_auth_passwords.json\n' "${AIRFLOW_HOME_DIR}"
        printf '    查看口令（仅 root）： cat %s/simple_auth_passwords.json\n' "${AIRFLOW_HOME_DIR}"
    else
        log_error "存在失败项，请按上方提示排查"
        exit 1
    fi
}

main "$@" < /dev/null
exit $?
