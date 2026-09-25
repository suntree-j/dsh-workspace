#!/usr/bin/env bash
# ============================================================
# scripts/stop.sh — 停止全部服务
# ============================================================
#
# 用法：
#   bash scripts/stop.sh              # 停止并移除容器（保留数据卷）
#   bash scripts/stop.sh --volumes    # 同时删除数据卷（会丢失全部数据！）
#
# 等价于：
#   docker compose down
#
# 默认**保留**命名卷，因此下次 start 后数据仍在。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

main() {
    local remove_volumes=0
    for arg in "$@"; do
        case "${arg}" in
            -v|--volumes)
                remove_volumes=1
                ;;
            -h|--help)
                sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
                return 0
                ;;
            *)
                log_error "未知参数：${arg}"
                exit 2
                ;;
        esac
    done

    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Data Platform — 停止服务${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'

    load_env
    require_docker

    if [ "${remove_volumes}" -eq 1 ]; then
        printf '\n'
        log_warn "!! 警告：--volumes 会删除全部数据卷 !!"
        log_warn "MySQL / Kafka / MinIO / Doris 的数据将全部丢失，且不可恢复。"
        printf '\n'
        printf '如果确认要继续，请输入大写 YES：'
        read -r confirm
        if [ "${confirm}" != "YES" ]; then
            log_info "已取消，未删除任何数据。"
            return 0
        fi
        printf '\n'
        log_info "停止容器并删除数据卷（docker compose down -v）..."
        compose down -v
        log_ok "容器与数据卷已删除。"
    else
        log_info "停止并移除容器（docker compose down，保留数据卷）..."
        compose down
        printf '\n'
        log_ok "容器已停止，数据卷已保留。"
        log_info "下次执行 bash scripts/start.sh 后数据仍然存在。"
    fi

    printf '\n'
    printf '当前数据卷：\n'
    docker volume ls --filter "name=data-platform" --format '  {{.Name}}' || true
    printf '\n'
}

main "$@"
