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
# 探测对外访问 IP
#
# 为什么要单独探测：云服务器的 `hostname -I` 返回的是**内网地址**
# （实测 172.16.0.10），把它当访问地址打印出来会误导使用者。
# 顺序：显式环境变量 → 公网回显服务 → 内网地址（离线环境的兜底）。
# ------------------------------------------------------------
public_ip() {
    if [ -n "${PUBLIC_IP:-}" ]; then
        printf '%s' "${PUBLIC_IP}"
        return 0
    fi

    local candidate
    for endpoint in "https://ifconfig.me/ip" "https://ipinfo.io/ip"; do
        candidate="$(curl -fsS --max-time 5 "${endpoint}" 2>/dev/null | tr -d '[:space:]')"
        case "${candidate}" in
            [0-9]*.[0-9]*.[0-9]*.[0-9]*) printf '%s' "${candidate}"; return 0 ;;
        esac
    done

    hostname -I 2>/dev/null | awk '{print $1}'
}

# ------------------------------------------------------------
# 服务层入口：协议探测与 curl 包装
#
# 背景：Sprint 7 之后服务层的**对外入口是 HTTPS**（见 deploy/nginx/
#       data-platform.conf 顶部说明：明文 HTTP 在公网链路中会被中间
#       设备改写，约 40% 的请求变成没有 Server 头的空 502）。
#       80 端口从此只保留健康探针，其余一律 302 跳转到 443。
#
# 为什么不把 https 写死在各个脚本里：
#   证书由 scripts/setup-tls.sh 在宿主机上生成，属于**机器状态**而非
#   仓库内容。若在一台还没跑过 setup-tls.sh 的机器上（例如刚克隆下来
#   做本地验证）硬测 https，脚本会因"没有证书"而失败 —— 那是环境缺失，
#   不是功能缺陷，会让验收结论失真。
#   因此这里做探测：有证书 → 用 https（用户真实入口）；无证书 → 回落 http。
# ------------------------------------------------------------
TLS_CERT_PATH="/etc/ssl/data-platform/server.crt"
TLS_KEY_PATH="/etc/ssl/data-platform/server.key"

# 站点协议：由 .env 的 SITE_SCHEME 决定，默认 http
#
# !! 为什么不再"证书存在就用 https" !!
#   Sprint 7 期间曾按"证书存在即走 https"实现（当时的理由是明文 HTTP
#   在公网链路上被中间设备改写，约 40% 请求变成空 502）。
#   后来项目负责人决定**暂不使用 TLS**：按 IP 访问只能自签，
#   浏览器每个会话都要点一次"继续前往"，演示观感不好；
#   等注册域名、申请证书、完成备案之后再启用。
#
#   实测支持这个决定：把客户端 VPN 关掉后重测，明文 HTTP **30/30 正常**，
#   说明当初改写响应的中间设备在 VPN 出口路径上，而不在这条 IP 直连路径上。
#
#   于是把判断依据从"机器状态"改成"显式配置"：
#   **配置表达意图，证书只是产物** —— 机器上有证书，不等于"现在想用 https"。
#   启用 TLS 时只需在 .env 里把 SITE_SCHEME 改成 https。
site_scheme() {
    printf '%s' "${SITE_SCHEME:-http}"
}

# 站点根地址，例如 https://127.0.0.1 或 http://127.0.0.1
site_base() {
    printf '%s://127.0.0.1' "$(site_scheme)"
}

# curl 包装：URL 是 https 时自动跳过证书校验
#   为什么这里允许 -k：证书是**自签**的（项目按 IP 访问，受信任的 CA
#   不为裸 IP 签发证书），而本函数只用于**自有站点的本机自检**
#   （127.0.0.1），不经过任何公网链路，不存在中间人风险。
#   访问第三方公网地址时不要用这个包装。
curl_site() {
    case "$*" in
        *https://*) curl -k "$@" ;;
        *)          curl "$@" ;;
    esac
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
