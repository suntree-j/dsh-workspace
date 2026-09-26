#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-0.sh — Sprint 0 全链路验收脚本
# ============================================================
#
# 用法（在仓库根目录）：
#   bash scripts/verify-sprint-0.sh
#
# 依次执行 SPRINT_0.md 第 23、29 节要求的全部验收步骤：
#   1. 环境检查（docker / compose 是否可用）
#   2. docker compose config        校验编排文件
#   3. docker compose up -d         启动服务
#   4. docker compose ps            查看状态
#   5. bash scripts/health-check.sh 健康检查
#   6. python -m pytest             单元 + 冒烟测试
#
# 全部通过则输出汇总并 exit 0；任一步失败则 exit 1。
#
# 前置条件：
#   - Docker Desktop 已安装并显示 Engine running
#   - 已执行 cp .env.example .env
#
# 注意：首次运行需要拉取约 5 GB 镜像，并等待 Doris 初始化（1~3 分钟）。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

# ------------------------------------------------------------
# 结果记录
# ------------------------------------------------------------
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
    if [ "$2" != "PASS" ]; then
        OVERALL=1
    fi
}

# ------------------------------------------------------------
# 解析 python 解释器
#
# 优先使用 data-generator/.venv（依赖已装好），
# 否则回退到 python3 / python。
# ------------------------------------------------------------
resolve_python() {
    # 依次尝试：仓库根 .venv（服务器上由部署脚本创建）→ data-generator/.venv
    # → 系统 python3 / python。两个位置都要覆盖 Windows(Scripts) 与 Linux(bin)。
    local candidate
    for candidate in \
        "${REPO_ROOT}/.venv/bin/python" \
        "${REPO_ROOT}/.venv/Scripts/python.exe" \
        "${REPO_ROOT}/data-generator/.venv/bin/python" \
        "${REPO_ROOT}/data-generator/.venv/Scripts/python.exe"; do
        if [ -x "${candidate}" ]; then
            printf '%s' "${candidate}"
            return 0
        fi
    done
    command -v python3 >/dev/null 2>&1 && { printf 'python3'; return 0; }
    command -v python >/dev/null 2>&1 && { printf 'python'; return 0; }
    printf ''
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 0 全链路验收${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env

    # --------------------------------------------------------
    # 1. 环境检查
    # --------------------------------------------------------
    step_start "1/6 环境检查"
    require_docker
    log_ok "docker 可用：$(docker --version)"
    log_ok "compose 可用：$(docker compose version --short 2>/dev/null || echo 'v2')"
    local python_bin
    python_bin="$(resolve_python)"
    if [ -n "${python_bin}" ]; then
        log_ok "python 可用：${python_bin}"
    else
        log_warn "未找到 python，将跳过测试步骤"
    fi
    step_record "环境检查" "PASS"

    # --------------------------------------------------------
    # 2. docker compose config
    # --------------------------------------------------------
    step_start "2/6 docker compose config（校验编排文件）"
    if compose config --quiet; then
        log_ok "docker compose config 校验通过"
        step_record "docker compose config" "PASS"
    else
        log_error "docker compose config 校验失败"
        step_record "docker compose config" "FAIL"
        summary
        return 1
    fi

    # --------------------------------------------------------
    # 3. docker compose up -d
    # --------------------------------------------------------
    step_start "3/6 docker compose up -d（启动服务）"
    log_info "首次运行需拉取镜像，请耐心等待 ..."
    if compose up -d; then
        log_ok "容器已启动"
        step_record "docker compose up -d" "PASS"
    else
        log_error "docker compose up -d 失败"
        log_error "排查： docker compose logs --tail=100"
        step_record "docker compose up -d" "FAIL"
        summary
        return 1
    fi

    # --------------------------------------------------------
    # 4. docker compose ps
    # --------------------------------------------------------
    step_start "4/6 docker compose ps（查看状态）"
    compose ps
    step_record "docker compose ps" "PASS"

    # --------------------------------------------------------
    # 5. 健康检查
    # --------------------------------------------------------
    step_start "5/6 健康检查（等待服务就绪）"
    log_info "Doris 首次启动较慢，最多等待 300 秒 ..."

    local deadline=$(( SECONDS + 300 ))
    local healthy=0
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        if bash "${REPO_ROOT}/scripts/health-check.sh" >/dev/null 2>&1; then
            healthy=1
            break
        fi
        printf '\r%b' "${C_BLUE}[INFO]${C_RESET} 等待服务就绪 ... (${SECONDS}s)"
        sleep 10
    done
    printf '\r\033[K'

    if bash "${REPO_ROOT}/scripts/health-check.sh"; then
        step_record "health check" "PASS"
    else
        log_error "健康检查未通过"
        log_error "排查： docker compose logs --tail=100 doris-fe doris-be"
        step_record "health check" "FAIL"
    fi

    # --------------------------------------------------------
    # 6. pytest
    # --------------------------------------------------------
    step_start "6/6 python -m pytest（测试）"
    if [ -z "${python_bin}" ]; then
        log_warn "未找到 python，跳过测试"
        step_record "pytest" "SKIP"
    else
        if ( cd "${REPO_ROOT}" && "${python_bin}" -m pytest -q ); then
            log_ok "测试全部通过"
            step_record "pytest" "PASS"
        else
            log_error "测试未通过"
            step_record "pytest" "FAIL"
        fi
    fi

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
        printf '%b\n' "${C_GREEN}${C_BOLD} Sprint 0 验收通过${C_RESET}"
        printf '\n'
        printf '下一步可生成数据：\n'
        printf '  docker compose run --rm data-generator python -m src.generate_mysql_data\n'
        printf '  docker compose run --rm data-generator python -m src.generate_events\n'
    else
        printf '%b\n' "${C_RED}${C_BOLD} Sprint 0 验收未通过，请按上方提示排查${C_RESET}"
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
}

main "$@" < /dev/null
exit $?
