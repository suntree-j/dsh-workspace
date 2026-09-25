#!/usr/bin/env bash
# ============================================================
# scripts/status.sh — 查看服务状态
# ============================================================
#
# 用法：
#   bash scripts/status.sh
#
# 等价于：
#   docker compose ps
#
# 额外输出：
#   - 各服务镜像与版本
#   - 数据卷
#   - 端口映射
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Data Platform — 服务状态${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'

    load_env
    require_docker

    log_info "docker compose ps"
    printf '\n'
    compose ps --all

    printf '\n'
    log_info "运行中的容器与镜像"
    printf '\n'
    docker ps \
        --filter "label=com.docker.compose.project=data-platform" \
        --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' \
        || true

    printf '\n'
    log_info "数据卷"
    printf '\n'
    docker volume ls --filter "name=data-platform" --format '  {{.Name}}' || true

    printf '\n'
    log_info "镜像"
    printf '\n'
    printf '  %-34s %s\n' "mysql:$(env_or MYSQL_VERSION 8.4.11)" "(MySQL $(env_or MYSQL_VERSION 8.4.11))"
    printf '  %-34s %s\n' "apache/kafka:$(env_or KAFKA_VERSION 4.2.1)" "(Kafka $(env_or KAFKA_VERSION 4.2.1))"
    printf '  %-34s %s\n' "$(env_or MINIO_IMAGE coollabsio/minio):$(env_or MINIO_VERSION RELEASE.2025-10-15T17-29-55Z)" "(MinIO)"
    printf '  %-34s %s\n' "apache/doris:fe-$(env_or DORIS_VERSION 4.1.4)" "(Doris FE)"
    printf '  %-34s %s\n' "apache/doris:be-$(env_or DORIS_VERSION 4.1.4)" "(Doris BE)"

    printf '\n'
    log_info "提示：健康检查请执行 bash scripts/health-check.sh"
    printf '\n'
}

main "$@"
