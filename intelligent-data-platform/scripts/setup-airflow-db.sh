#!/usr/bin/env bash
# ============================================================
# scripts/setup-airflow-db.sh — 供给 Airflow 元数据库（Sprint 4）
# ============================================================
#
# 用法（服务器上，仓库根目录，需要 root）：
#   bash scripts/setup-airflow-db.sh
#
# 幂等：库与用户用 IF NOT EXISTS / ALTER 处理，重复执行不报错。
#
# !! 为什么用 MySQL 而不是 PostgreSQL !!
#   Airflow 官方"首选"是 PostgreSQL，但 AGENTS.md 2.3 明确禁止未经批准
#   引入 PostgreSQL。而 Airflow 3.3.2 官方文档写得很清楚：
#       MySQL: 8.0, 8.4, Innovation
#   本机正是 MySQL 8.4.11，也就是说**不需要引入任何新组件**就能满足要求。
#   为了照抄官方推荐而违反自己的规范，是把"选型"做成了"抄作业"。
#
#   顺带记一条坑：Airflow 官方**明确不支持 MariaDB**（索引处理有已知问题，
#   官方不测 MariaDB 的迁移脚本）。本项目用的是真 MySQL，不踩这个坑。
#
# !! 为什么给独立的库 + 独立账号 + 只授权一个库 !!
#   与 Sprint 6 的只读账号 agent_ro 是同一个道理：最小权限。
#   Airflow 确实需要 DDL 权限，但只需要在**它自己的元数据库**里。
#   它没有任何理由读 `ecommerce` 业务库 —— 本脚本会实测这一点。
#
# 本脚本**从不打印口令**：口令随机生成后只写进 .env（已是 .gitignore）。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

ENV_FILE="${REPO_ROOT}/.env"
MYSQL_SERVICE="mysql"
DB_NAME="airflow"
DB_USER="airflow"

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "需要 root： sudo bash scripts/setup-airflow-db.sh"
        exit 1
    fi
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 供给 Airflow 元数据库（MySQL）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    require_root
    load_env

    [ -f "${ENV_FILE}" ] || { log_error "找不到 ${ENV_FILE}"; exit 1; }

    # ---------- 口令：已有就复用，没有就生成 ----------
    local existing_pw pw
    existing_pw="$(grep -E '^AIRFLOW_DB_PASSWORD=' "${ENV_FILE}" | head -1 | cut -d= -f2- || true)"
    if [ -n "${existing_pw}" ]; then
        pw="${existing_pw}"
        log_info "复用 .env 中已有的 AIRFLOW_DB_PASSWORD"
    else
        pw="$(openssl rand -hex 16)"
        log_info "已生成随机口令（不打印）"
    fi

    local root_pw="${MYSQL_ROOT_PASSWORD:-}"
    [ -n "${root_pw}" ] || { log_error ".env 中缺少 MYSQL_ROOT_PASSWORD"; exit 1; }

    # ---------- 建库建号（幂等） ----------
    log_info "创建库 ${DB_NAME} 与账号 ${DB_USER}（幂等）"
    docker exec -i -e MYSQL_PWD="${root_pw}" "${MYSQL_SERVICE}" \
        mysql -uroot --default-character-set=utf8mb4 <<SQL
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\`
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'%' IDENTIFIED BY '${pw}';
ALTER USER '${DB_USER}'@'%' IDENTIFIED BY '${pw}';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'%';
FLUSH PRIVILEGES;
SQL

    # ---------- 三项验证：能连、能建表、进不去业务库 ----------
    log_info "验证 ${DB_USER} 账号"
    local ver
    ver="$(docker exec -i -e MYSQL_PWD="${pw}" "${MYSQL_SERVICE}" \
        mysql -u"${DB_USER}" -N -e "SELECT VERSION();" 2>&1 | head -1)"
    if [ -n "${ver}" ]; then
        log_ok "可连接（MySQL ${ver}）"
    else
        log_error "无法连接，请检查授权"
        exit 1
    fi

    if docker exec -i -e MYSQL_PWD="${pw}" "${MYSQL_SERVICE}" mysql -u"${DB_USER}" -N -e \
            "USE ${DB_NAME}; CREATE TABLE IF NOT EXISTS _privcheck (id INT PRIMARY KEY); DROP TABLE _privcheck;" \
            >/dev/null 2>&1; then
        log_ok "在自己的库内可执行 DDL（Airflow 迁移需要）"
    else
        log_error "在自己的库内无法建表，迁移会失败"
        exit 1
    fi

    # 这一条是"最小权限"的实证：拒绝才是正确结果
    if docker exec -i -e MYSQL_PWD="${pw}" "${MYSQL_SERVICE}" \
            mysql -u"${DB_USER}" -N -e "SELECT COUNT(*) FROM ecommerce.orders;" >/dev/null 2>&1; then
        log_error "该账号竟然能读 ecommerce —— 权限过宽，请收紧授权"
        exit 1
    else
        log_ok "对 ecommerce 业务库被拒绝（符合最小权限）"
    fi

    # ---------- 落盘到 .env（只追加，绝不覆盖） ----------
    if [ -z "${existing_pw}" ]; then
        {
            printf '\n# ---------- Sprint 4: Airflow 元数据库（MySQL，非 PostgreSQL）----------\n'
            printf 'AIRFLOW_DB_HOST=127.0.0.1\n'
            printf 'AIRFLOW_DB_PORT=3306\n'
            printf 'AIRFLOW_DB_NAME=%s\n' "${DB_NAME}"
            printf 'AIRFLOW_DB_USER=%s\n' "${DB_USER}"
            printf 'AIRFLOW_DB_PASSWORD=%s\n' "${pw}"
        } >> "${ENV_FILE}"
        log_ok "已追加 AIRFLOW_DB_* 到 .env（5 项）"
    else
        log_ok ".env 中已存在 AIRFLOW_DB_PASSWORD，未改动"
    fi

    printf '\n'
    log_ok "元数据库就绪：库 ${DB_NAME} / 账号 ${DB_USER}"
    log_info "下一步： bash scripts/deploy-airflow.sh"
}

main "$@" < /dev/null
exit $?
