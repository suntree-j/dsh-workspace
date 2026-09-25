#!/usr/bin/env bash
# ============================================================
# scripts/start.sh — 启动全部基础服务
# ============================================================
#
# 用法：
#   bash scripts/start.sh
#
# 等价于：
#   docker compose up -d
#
# 启动后会等待各服务健康，并提示下一步命令。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Data Platform — 启动服务${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'

    load_env
    require_docker

    log_info "启动容器（docker compose up -d）..."
    printf '\n'

    if ! compose up -d; then
        log_error "docker compose up -d 失败。"
        log_error "请查看日志定位原因："
        log_error "  docker compose logs --tail=100"
        exit 1
    fi

    printf '\n'
    log_ok "容器已启动。当前状态："
    printf '\n'
    compose ps

    printf '\n'
    log_info "等待服务就绪（最多 300 秒）..."
    printf '\n'

    # 依赖容器自身的 healthcheck，而不是固定 sleep
    local deadline=$(( SECONDS + 300 ))
    local all_healthy=0

    while [ "${SECONDS}" -lt "${deadline}" ]; do
        local unhealthy
        unhealthy="$(compose ps --format '{{.Name}} {{.State}} {{.Health}}' 2>/dev/null \
            | grep -v -E ' ([a-z-]+ )?healthy$' \
            | grep -v '^minio-init ' \
            | grep -v '^kafka-init ' \
            || true)"

        if [ -z "${unhealthy}" ]; then
            all_healthy=1
            break
        fi

        printf '\r%b' "${C_BLUE}[INFO]${C_RESET} 仍有服务未就绪，继续等待 ... (${SECONDS}s)"
        sleep 5
    done

    # 清理进度行
    printf '\r\033[K'

    if [ "${all_healthy}" -eq 1 ]; then
        log_ok "全部服务已就绪"
    else
        log_warn "仍有服务未就绪（可能仍在初始化，Doris 首次启动较慢）"
        log_warn "请执行以下命令查看详情："
        log_warn "  bash scripts/status.sh"
        log_warn "  bash scripts/health-check.sh"
    fi

    printf '\n'
    printf '%b\n' "${C_BOLD}下一步：${C_RESET}"
    printf '  bash scripts/status.sh          查看服务状态\n'
    printf '  bash scripts/health-check.sh    健康检查\n'
    printf '  bash scripts/stop.sh            停止服务\n'
    printf '\n'
    printf '服务端口：\n'
    printf '  MySQL        localhost:%s\n' "$(env_or MYSQL_HOST_PORT 3306)"
    printf '  Kafka        kafka:9092 (容器内) / localhost:%s (宿主机)\n' "$(env_or KAFKA_EXTERNAL_PORT 19092)"
    printf '  MinIO API    http://localhost:%s\n' "$(env_or MINIO_API_HOST_PORT 9000)"
    printf '  MinIO 控制台  http://localhost:%s\n' "$(env_or MINIO_CONSOLE_HOST_PORT 9001)"
    printf '  Doris FE     http://localhost:%s   (MySQL 协议 %s)\n' \
        "$(env_or DORIS_FE_HTTP_PORT 8030)" "$(env_or DORIS_FE_QUERY_PORT 9030)"
    printf '  Doris BE     http://localhost:%s\n' "$(env_or DORIS_BE_HTTP_PORT 8040)"
    printf '\n'
}

main "$@"
