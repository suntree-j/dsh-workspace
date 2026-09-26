#!/usr/bin/env bash
# ============================================================
# scripts/lib/common.sh — 各脚本共用的工具函数
# ============================================================
#
# 被 start.sh / stop.sh / status.sh / health-check.sh 引用。
# 不由用户直接执行。
# ============================================================

# 严格模式：未定义变量报错、管道失败传播
set -euo pipefail

# ------------------------------------------------------------
# 仓库根目录（本文件位于 <repo>/scripts/lib/）
# ------------------------------------------------------------
_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${_SCRIPT_DIR}/../.." && pwd)"
export REPO_ROOT

# ------------------------------------------------------------
# 颜色（仅在 TTY 下启用）
# ------------------------------------------------------------
if [ -t 1 ]; then
    C_RED='\033[0;31m'
    C_GREEN='\033[0;32m'
    C_YELLOW='\033[0;33m'
    C_BLUE='\033[0;34m'
    C_BOLD='\033[1m'
    C_RESET='\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''; C_RESET=''
fi
export C_RED C_GREEN C_YELLOW C_BLUE C_BOLD C_RESET

# ------------------------------------------------------------
# 日志
# ------------------------------------------------------------
log_info()  { printf '%b\n' "${C_BLUE}[INFO]${C_RESET} $*"; }
log_ok()    { printf '%b\n' "${C_GREEN}[ OK ]${C_RESET} $*"; }
log_warn()  { printf '%b\n' "${C_YELLOW}[WARN]${C_RESET} $*" >&2; }
log_error() { printf '%b\n' "${C_RED}[FAIL]${C_RESET} $*" >&2; }

# ------------------------------------------------------------
# 加载 .env
#
# 说明：使用 `set -a` 让变量自动导出给子进程（docker compose 需要）。
#       不覆盖已存在的环境变量，便于临时覆盖。
# ------------------------------------------------------------
load_env() {
    local env_file="${REPO_ROOT}/.env"
    if [ ! -f "${env_file}" ]; then
        log_error "找不到 ${env_file}"
        log_error "请先执行： cp .env.example .env  然后按需修改口令"
        exit 1
    fi

    # 逐行解析，避免 `source` 执行任意代码；
    # 只接受 KEY=VALUE 形式，忽略注释与空行。
    while IFS= read -r line || [ -n "${line}" ]; do
        # 去掉行首空白
        line="${line#"${line%%[![:space:]]*}"}"
        case "${line}" in
            ''|'#'*) continue ;;
        esac
        case "${line}" in
            *=*) ;;
            *) continue ;;
        esac
        local key="${line%%=*}"
        local value="${line#*=}"
        # 去掉 key 尾部空白
        key="${key%"${key##*[![:space:]]}"}"
        # 校验 key 合法性
        case "${key}" in
            [A-Za-z_][A-Za-z0-9_]*) ;;
            *) continue ;;
        esac
        # 去掉 value 两端引号
        case "${value}" in
            \"*\") value="${value#\"}" ; value="${value%\"}" ;;
            \'*\') value="${value#\'}" ; value="${value%\'}" ;;
        esac
        # 已存在的环境变量优先（允许临时覆盖）
        if [ -z "$(eval "printf '%s' \"\${${key}+set}\"")" ]; then
            export "${key}=${value}"
        fi
    done < "${env_file}"
}

# ------------------------------------------------------------
# 变量默认值
# ------------------------------------------------------------
env_or() {
    local name="$1"
    local default="$2"
    local current
    current="$(eval "printf '%s' \"\${${name}:-}\"")"
    if [ -z "${current}" ]; then
        printf '%s' "${default}"
    else
        printf '%s' "${current}"
    fi
}

# ------------------------------------------------------------
# 检查 docker CLI 与 daemon
# ------------------------------------------------------------
require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        # Docker Desktop 在 Windows 上可能未加入当前 shell 的 PATH
        local fallback="/c/Program Files/Docker/Docker/resources/bin/docker.exe"
        if [ -x "${fallback}" ]; then
            export PATH="$(dirname "${fallback}"):${PATH}"
        fi
    fi

    if ! command -v docker >/dev/null 2>&1; then
        log_error "找不到 docker 命令。"
        log_error "请安装 Docker Desktop 并确保已启动。"
        exit 1
    fi

    if ! docker info >/dev/null 2>&1; then
        log_error "无法连接 Docker daemon。"
        log_error "请先启动 Docker Desktop，并等待其显示 Engine running。"
        exit 1
    fi
}

# ------------------------------------------------------------
# docker compose 包装
# ------------------------------------------------------------
compose() {
    docker compose -f "${REPO_ROOT}/docker-compose.yml" --project-directory "${REPO_ROOT}" "$@"
}

# ------------------------------------------------------------
# 在容器内执行命令；容器未运行时返回非 0
# ------------------------------------------------------------
container_running() {
    local name="$1"
    [ "$(docker inspect -f '{{.State.Running}}' "${name}" 2>/dev/null || echo false)" = "true" ]
}

# ------------------------------------------------------------
# 服务名 -> 容器名（本项目显式指定了 container_name）
# ------------------------------------------------------------
service_container() {
    printf '%s' "$1"
}

# ------------------------------------------------------------
# 重试执行命令直到成功或超时
#   retry <超时秒> <间隔秒> <命令...>
# ------------------------------------------------------------
retry() {
    local timeout="$1"; shift
    local interval="$1"; shift
    local elapsed=0

    while [ "${elapsed}" -lt "${timeout}" ]; do
        if "$@" >/dev/null 2>&1; then
            return 0
        fi
        sleep "${interval}"
        elapsed=$(( elapsed + interval ))
    done
    # 最后一次尝试，保留输出供诊断
    "$@"
}
