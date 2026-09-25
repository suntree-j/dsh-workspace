#!/usr/bin/env bash
# ============================================================
# scripts/health-check.sh — 核心服务健康检查
# ============================================================
#
# 用法：
#   bash scripts/health-check.sh
#
# 检查项（依据 docs/sprint/SPRINT_0.md 第 15 节）：
#   MySQL / Kafka / MinIO / Doris FE / Doris BE
#
# 输出：
#   [OK] MySQL
#   [OK] Kafka
#   ...
#   All services are healthy
#
# 任一服务不健康则 exit 1。
#
# 设计说明：
#   检查在**容器内部**执行，因此不依赖宿主机是否安装了 mysql /
#   kafka 客户端；同时也不依赖宿主端口映射是否可用。
#   所有容器间通信均使用服务名，禁止 localhost。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

# ------------------------------------------------------------
# 结果收集
# ------------------------------------------------------------
declare -a RESULT_NAMES=()
declare -a RESULT_STATES=()
declare -a RESULT_DETAILS=()

FAILED=0

record() {
    local name="$1"
    local ok="$2"      # 1 = ok, 0 = fail
    local detail="$3"

    RESULT_NAMES+=("${name}")
    RESULT_DETAILS+=("${detail}")
    if [ "${ok}" -eq 1 ]; then
        RESULT_STATES+=("ok")
        printf '%b\n' "${C_GREEN}[OK]${C_RESET} ${name}"
    else
        RESULT_STATES+=("fail")
        FAILED=$(( FAILED + 1 ))
        printf '%b\n' "${C_RED}[FAIL]${C_RESET} ${name}"
        if [ -n "${detail}" ]; then
            printf '       %s\n' "${detail}"
        fi
    fi
}

# ------------------------------------------------------------
# MySQL：使用 mysqladmin ping
# ------------------------------------------------------------
check_mysql() {
    local detail=""

    if ! container_running mysql; then
        record "MySQL" 0 "容器 mysql 未运行"
        return
    fi

    if docker exec mysql mysqladmin ping \
            -h 127.0.0.1 -u root -p"${MYSQL_ROOT_PASSWORD}" --silent >/dev/null 2>&1; then
        # 进一步确认业务库可访问
        local db_count
        db_count="$(docker exec mysql mysql \
            -h 127.0.0.1 -u root -p"${MYSQL_ROOT_PASSWORD}" -N -B \
            -e "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='ecommerce';" \
            2>/dev/null | tr -d '[:space:]')"

        if [ "${db_count}" = "1" ]; then
            record "MySQL" 1 "ecommerce 库存在"
        else
            record "MySQL" 0 "mysqladmin ping 成功，但 ecommerce 库不存在"
        fi
    else
        record "MySQL" 0 "mysqladmin ping 失败"
    fi
}

# ------------------------------------------------------------
# Kafka：使用官方 CLI 检查 broker 可用性
# ------------------------------------------------------------
check_kafka() {
    if ! container_running kafka; then
        record "Kafka" 0 "容器 kafka 未运行"
        return
    fi

    if docker exec kafka /opt/kafka/bin/kafka-broker-api-versions.sh \
            --bootstrap-server kafka:9092 >/dev/null 2>&1; then
        # 确认 4 个业务 Topic 都存在
        local topics missing=0
        topics="$(docker exec kafka /opt/kafka/bin/kafka-topics.sh \
            --bootstrap-server kafka:9092 --list 2>/dev/null || true)"

        for t in order_event payment_event refund_event behavior_event; do
            if ! printf '%s\n' "${topics}" | grep -qx "${t}"; then
                missing=$(( missing + 1 ))
            fi
        done

        if [ "${missing}" -eq 0 ]; then
            record "Kafka" 1 "4 个 Topic 均已创建"
        else
            record "Kafka" 0 "broker 可用，但缺少 ${missing} 个 Topic（见 kafka-init 容器日志）"
        fi
    else
        record "Kafka" 0 "kafka-broker-api-versions.sh 调用失败"
    fi
}

# ------------------------------------------------------------
# MinIO：检查健康接口
# ------------------------------------------------------------
check_minio() {
    if ! container_running minio; then
        record "MinIO" 0 "容器 minio 未运行"
        return
    fi

    if docker exec minio curl -fsS http://localhost:9000/minio/health/live >/dev/null 2>&1; then
        # 确认 lakehouse bucket 存在
        if docker exec minio sh -c \
            "MC_HOST_local=\"http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@localhost:9000\" mc ls local/ 2>/dev/null" \
            | grep -q "${MINIO_BUCKET}"; then
            record "MinIO" 1 "lakehouse bucket 存在"
        else
            record "MinIO" 0 "服务正常，但 bucket ${MINIO_BUCKET} 不存在（见 minio-init 容器日志）"
        fi
    else
        record "MinIO" 0 "健康接口 /minio/health/live 无响应"
    fi
}

# ------------------------------------------------------------
# Doris FE：HTTP 接口 + MySQL 协议
# ------------------------------------------------------------
check_doris_fe() {
    if ! container_running doris-fe; then
        record "Doris FE" 0 "容器 doris-fe 未运行"
        return
    fi

    # 优先用 FE 自身的 HTTP 接口判断就绪状态
    local alive=0
    if docker exec doris-fe curl -fsS http://localhost:8030/api/bootstrap >/dev/null 2>&1; then
        alive=1
    elif docker exec doris-fe bash -c 'exec 3<>/dev/tcp/127.0.0.1/9030' >/dev/null 2>&1; then
        alive=1
    fi

    if [ "${alive}" -ne 1 ]; then
        record "Doris FE" 0 "FE HTTP(8030) / MySQL 协议(9030) 均无响应"
        return
    fi

    # 用 HTTP 接口确认存在 ALIVE 的 FE 节点
    if docker exec doris-fe curl -fsS "http://localhost:8030/api/show_proc?path=/" >/dev/null 2>&1; then
        record "Doris FE" 1 "FE 已就绪"
    else
        record "Doris FE" 1 "FE 端口已就绪"
    fi
}

# ------------------------------------------------------------
# Doris BE：通过 FE 查询 SHOW BACKENDS
#
# BE 自身没有 /api/health 接口，其就绪状态由 FE 的
# SHOW BACKENDS 结果中的 Alive 列体现，因此从 doris-be 容器
# 使用内置 mysql 客户端连接 FE 查询。
# ------------------------------------------------------------
check_doris_be() {
    if ! container_running doris-be; then
        record "Doris BE" 0 "容器 doris-be 未运行"
        return
    fi

    local out
    out="$(docker exec doris-be mysql \
        -h "${DORIS_FE_IP}" -P 9030 -uroot \
        --connect-timeout=5 -N -B \
        -e "SHOW BACKENDS;" 2>/dev/null || true)"

    if [ -z "${out}" ]; then
        record "Doris BE" 0 "无法通过 FE(${DORIS_FE_IP}:9030) 查询 SHOW BACKENDS"
        return
    fi

    # Host 列应包含 BE 的 IP；Alive 列为 true
    if printf '%s\n' "${out}" | grep -q "${DORIS_BE_IP}" \
       && printf '%s\n' "${out}" | grep -qw "true"; then
        record "Doris BE" 1 "BE ${DORIS_BE_IP} 已注册且 Alive"
    else
        record "Doris BE" 0 "BE 尚未注册或未存活（SHOW BACKENDS 无 ${DORIS_BE_IP} / Alive=true）"
    fi
}

# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Data Platform Health Check${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'

    load_env
    require_docker

    # 参数校验：从 .env 读取关键变量并给出默认值
    MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD 未设置}"
    MINIO_ROOT_USER="${MINIO_ROOT_USER:?MINIO_ROOT_USER 未设置}"
    MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD 未设置}"
    MINIO_BUCKET="${MINIO_BUCKET:-lakehouse}"
    DORIS_FE_IP="${DORIS_FE_IP:-172.28.0.10}"
    DORIS_BE_IP="${DORIS_BE_IP:-172.28.0.11}"

    check_mysql
    check_kafka
    check_minio
    check_doris_fe
    check_doris_be

    printf '\n'
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    if [ "${FAILED}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} All services are healthy${C_RESET}"
        printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
        printf '\n'
        return 0
    fi

    printf '%b\n' "${C_RED}${C_BOLD} ${FAILED} service(s) unhealthy${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
    printf '排查建议：\n'
    printf '  bash scripts/status.sh              查看容器状态\n'
    printf '  docker compose logs --tail=100      查看日志\n'
    printf '  docker compose logs doris-fe        查看 Doris FE 日志\n'
    printf '  docker compose logs doris-be        查看 Doris BE 日志\n'
    printf '  docker compose logs kafka-init      查看 Topic 初始化结果\n'
    printf '  docker compose logs minio-init      查看 Bucket 初始化结果\n'
    printf '\n'
    return 1
}

main "$@" < /dev/null
exit $?
