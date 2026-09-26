#!/usr/bin/env bash
# ============================================================
# scripts/install-airflow.sh — 安装 Apache Airflow 3.3.2（Sprint 4）
# ============================================================
#
# 用法（服务器上，仓库根目录，需要 root）：
#   bash scripts/install-airflow.sh
#
# 幂等：venv 已存在则复用；apt 与 pip 都会跳过已装好的部分。
#
# ------------------------------------------------------------
# 踩坑记录：MySQL 驱动这件事连续错了两次，完整记下来
#
# 最终正确做法（就一句话）：
#   装好 apt 编译依赖，然后 `pip install "apache-airflow[mysql]==3.3.2"`。
#   **不要手工裁剪 Airflow 的依赖集。**
#
# 为什么值得记：这条弯路有三个错误，每个都长得像"已经修好了"。
#
# 【错误 1】直接用 [mysql] extra，缺编译依赖 → 构建失败
#       Exception: Can not find valid pkg-config name.
#       ERROR: Failed to build 'mysqlclient'
#   这一步的教训是对的：mysqlclient 是 C 扩展，需要 pkg-config、
#   编译器、Python 头文件与 MySQL 客户端库头文件。
#
# 【错误 2】"聪明"地绕开 extra —— 我的推理是错的
#   我推断："元数据库用哪个驱动完全由 SQLAlchemy 连接串决定，
#   所以装核心包 + PyMySQL 就能避开 mysqlclient"。
#   报错打脸：
#       RuntimeError: You do not have `mysqlclient` package installed.
#   源码 airflow/settings.py 的 configure_adapters()：
#       if SQL_ALCHEMY_CONN.startswith("mysql"):
#           try:
#               import MySQLdb.converters
#           except ImportError:
#               raise RuntimeError("You do not have `mysqlclient` ...")
#   即：只要连接串以 mysql 开头，核心包就硬性要求 MySQLdb，没有开关。
#
# 【错误 3】把 PyMySQL 卸掉 —— 又一个连锁反应
#   既然 mysqlclient 成了硬要求，我判断 PyMySQL "没用了"就卸了。
#   紧接着报：
#       ModuleNotFoundError: No module named 'aiomysql'
#   因为 **Airflow 3 是 async 优先的**，airflow/settings.py:244 写着：
#       AIO_LIBS_MAPPING = {"sqlite": "aiosqlite", "postgresql": "asyncpg",
#                           "mysql": "aiomysql"}
#   而 aiomysql 本身依赖 PyMySQL。所以三者缺一不可。
#
# 真相：[mysql] extra 拉的 apache-airflow-providers-mysql，依赖正好是
#       mysqlclient>=2.2.5 + aiomysql>=0.2.0 + pymysql<1.2,>=1.0.3
#   —— 官方打包早就配齐了整套驱动。我想省掉的那一步，正是把
#   "框架已经解决好的问题"重新手工解决了一遍，然后解决错了。
#
# 【顺带一个必须实测的点】拿到的是谁的客户端库
#   Ubuntu 上 default-libmysqlclient-dev 有时解析到 MariaDB 的客户端库，
#   而 MariaDB 客户端库与 MySQL 8 的 caching_sha2_password 有过兼容问题。
#   本项目用的是真 MySQL 8.4.11，所以必须实测而不是假设。
#   实测（脚本会打印）：
#       mysql_config --version → 8.0.46
#       mysql_config --libs    → -lmysqlclient -lz -lzstd -lssl -lcrypto
#   是 Oracle 真库，认证一次通过。
#
# ------------------------------------------------------------
# 为什么 Airflow 用独立的 venv（.venv-airflow）
#   不只是"讲卫生"，而是**硬冲突**，已实测：
#       Airflow 3.3.2 依赖  fastapi>=0.129.0,<0.137.0
#       数据服务已装        fastapi 0.141.1
#   装在一起必然要把线上看板的接口依赖降级。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

AIRFLOW_VERSION="3.3.2"
VENV_DIR="${REPO_ROOT}/.venv-airflow"
PIP="${VENV_DIR}/bin/pip"
INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"

APT_PACKAGES=(
    pkg-config          # mysqlclient 的构建脚本用 pkg-config 找 MySQL 头文件
    build-essential     # C 编译器
    python3-dev         # Python 头文件
    default-libmysqlclient-dev   # mysql_config + MySQL 客户端库
)

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 安装 Apache Airflow ${AIRFLOW_VERSION}（Sprint 4）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    if [ "$(id -u)" -ne 0 ]; then
        log_error "需要 root： sudo bash scripts/install-airflow.sh"
        exit 1
    fi

    # ---------- 1. venv ----------
    printf '\n%b\n' "${C_BOLD}▶ 1/4 创建独立虚拟环境${C_RESET}"
    if [ ! -x "${VENV_DIR}/bin/python" ]; then
        python3 -m venv "${VENV_DIR}"
        log_ok "已创建 ${VENV_DIR}"
    else
        log_ok "复用已有 ${VENV_DIR}"
    fi
    log_info "Python： $("${VENV_DIR}/bin/python" --version 2>&1)"

    # ---------- 2. apt 编译依赖 ----------
    printf '\n%b\n' "${C_BOLD}▶ 2/4 安装 mysqlclient 的编译依赖（apt）${C_RESET}"
    export DEBIAN_FRONTEND=noninteractive
    local missing=()
    local p
    for p in "${APT_PACKAGES[@]}"; do
        if dpkg -s "${p}" >/dev/null 2>&1; then
            log_ok "${p} 已安装"
        else
            missing+=("${p}")
        fi
    done
    if [ "${#missing[@]}" -gt 0 ]; then
        log_info "需要安装： ${missing[*]}"
        if apt-get install -y --no-install-recommends "${missing[@]}" 2>&1 | tail -4; then
            log_ok "apt 安装完成"
        else
            log_error "apt 安装失败，请检查网络与源"
            exit 1
        fi
    fi

    # 打印拿到的是哪个客户端库 —— 见坑 3，这一步是留给后来者的证据
    if command -v mysql_config >/dev/null 2>&1; then
        log_info "mysql_config 版本： $(mysql_config --version)"
        log_info "链接参数： $(mysql_config --libs | head -1)"
        local client_ver
        client_ver="$(mysql_config --version)"
        case "${client_ver}" in
            8.*|9.*) log_ok "拿到的是 MySQL 官方客户端库（caching_sha2_password 可用）" ;;
            *) log_warn "客户端库版本为 ${client_ver}，请确认与 MySQL 8.4 的认证插件兼容" ;;
        esac
    else
        log_error "找不到 mysql_config —— mysqlclient 无法编译"
        exit 1
    fi

    # ---------- 3. pip 安装 ----------
    printf '\n%b\n' "${C_BOLD}▶ 3/4 安装 Airflow 与 MySQL 驱动（pip）${C_RESET}"
    "${PIP}" install -q --upgrade pip -i "${INDEX}"

    # 用官方的 [mysql] extra：它拉的 providers-mysql 正好带齐
    # Airflow 3 需要的整套驱动（mysqlclient + aiomysql + pymysql）。
    # 编译依赖已在第 2 步备好 —— 这才是 v1 失败的真正原因。
    if "${PIP}" install --timeout 60 --retries 5 -i "${INDEX}" \
            "apache-airflow[mysql]==${AIRFLOW_VERSION}"; then
        log_ok "pip 安装完成"
    else
        log_error "pip 安装失败"
        exit 1
    fi

    # ---------- 4. 验证 ----------
    printf '\n%b\n' "${C_BOLD}▶ 4/4 验证${C_RESET}"
    local ver
    ver="$("${VENV_DIR}/bin/airflow" version 2>/dev/null | head -1)"
    if [ "${ver}" = "${AIRFLOW_VERSION}" ]; then
        log_ok "airflow version = ${ver}"
    else
        log_error "版本不符：期望 ${AIRFLOW_VERSION}，实得 ${ver:-<空>}"
        exit 1
    fi

    # 三个驱动缺一不可，逐项验：
    #   MySQLdb   → airflow/settings.py 的 configure_adapters() 硬性要求
    #   aiomysql  → Airflow 3 的 async 元数据库引擎（settings.py:244 的映射表）
    #   pymysql   → aiomysql 的底层依赖
    if "${VENV_DIR}/bin/python" - <<'PY'
import MySQLdb, aiomysql, pymysql
print("  MySQLdb         :", MySQLdb.version_info)
print("  客户端库        :", MySQLdb.get_client_info())
print("  aiomysql        :", aiomysql.__version__)
print("  pymysql         :", pymysql.__version__)
PY
    then
        log_ok "MySQL 驱动三件套齐全（MySQLdb / aiomysql / pymysql）"
    else
        log_error "MySQL 驱动缺失，元数据库迁移会失败"
        exit 1
    fi

    printf '\n'
    log_ok "Airflow ${AIRFLOW_VERSION} 安装完成"
    log_info "下一步： bash scripts/setup-airflow-db.sh   （供给 MySQL 元数据库）"
}

main "$@" < /dev/null
exit $?
