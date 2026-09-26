#!/usr/bin/env bash
# ============================================================
# scripts/health-check.sh — 核心服务健康检查
# ============================================================
#
# 用法：
#   bash scripts/health-check.sh
#
# 检查项：
#   Sprint 0：MySQL / Kafka / MinIO / Doris FE / Doris BE
#   Sprint 1：Flink 集群 / Flink SQL Gateway / 实时 Topic /
#             Doris 数仓表 / Routine Load 实时导入作业
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
#
#   Sprint 1 的组件在只部署 Sprint 0 的环境里并不存在。
#   为满足「服务未启动时必须优雅跳过而不是误报失败」，
#   脚本先探测 flink-jobmanager 容器：
#     - 未运行 -> 实时链路检查全部记为 [SKIP]，不计入失败；
#     - 运行中 -> 正常检查，任一项不通过即 exit 1。
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
SKIPPED=0

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

# 跳过：服务本 Sprint 未部署，不算失败也不掩盖问题（显式打印 [SKIP]）
record_skip() {
    local name="$1"
    local reason="$2"

    SKIPPED=$(( SKIPPED + 1 ))
    printf '%b\n' "${C_YELLOW}[SKIP]${C_RESET} ${name}"
    if [ -n "${reason}" ]; then
        printf '       %s\n' "${reason}"
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
# MinIO：健康检查 + bucket 存在性
#
# 注意：minio 镜像基于 BusyBox，**不含 curl / wget / nc / sed / grep**，
#       只有 sh 内建、busybox 基础工具与 /usr/bin/mc。
#       因此不能用 curl 探健康接口，只能用 mc。
#       同时 `mc ls` 成功即说明服务可用，因此把「服务可用」与
#       「bucket 存在」合并为一次探测的两种情况。
# ------------------------------------------------------------
check_minio() {
    if ! container_running minio; then
        record "MinIO" 0 "容器 minio 未运行"
        return
    fi

    # mc ls <alias>/<bucket>：服务可用且 bucket 存在 -> 退出 0
    if docker exec minio sh -c \
        "MC_HOST_local='http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@localhost:9000'; export MC_HOST_local; mc ls local/${MINIO_BUCKET} >/dev/null 2>&1"; then
        record "MinIO" 1 "服务可用，bucket ${MINIO_BUCKET} 存在"
        return
    fi

    # bucket 不存在时再判断服务本身是否可用
    if docker exec minio sh -c \
        "MC_HOST_local='http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@localhost:9000'; export MC_HOST_local; mc ls local >/dev/null 2>&1"; then
        record "MinIO" 0 "服务可用，但 bucket ${MINIO_BUCKET} 不存在（见 minio-init 容器日志）"
    else
        record "MinIO" 0 "mc 无法连接 MinIO，请检查 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD"
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

    # Host 列可能是 BE 的静态 IP（由 BE_ADDR 决定），
    # 也可能是容器主机名 —— Doris 存在 FQDN/IP 双注册行为
    # （日志可见：master_info.backend_ip: doris-be, hostname_to_ip: 172.28.0.11），
    # 因此两者都接受；Alive 列只需存在 true。
    if { printf '%s\n' "${out}" | grep -q "${DORIS_BE_IP}" \
         || printf '%s\n' "${out}" | grep -q "doris-be"; } \
       && printf '%s\n' "${out}" | grep -qw "true"; then
        record "Doris BE" 1 "BE 已注册且 Alive"
    else
        record "Doris BE" 0 "BE 尚未注册或未存活（SHOW BACKENDS 无匹配记录 / Alive=true）"
    fi
}

# ============================================================
# Sprint 1：实时数仓（Kafka → Flink → Doris）
# ============================================================

# 实时链路涉及的 8 个下游 Topic（DWD 4 + DWS 1 + ADS 3）
REALTIME_TOPICS="dwd_trade_order_detail dwd_trade_payment_detail \
dwd_trade_refund_detail dwd_traffic_behavior_detail \
dws_traffic_overview_1m \
ads_realtime_trade_1m ads_realtime_traffic_1m ads_realtime_category_1m"

# 实时链路涉及的 8 张 Doris 表（与 Topic 同名）
REALTIME_TABLES="${REALTIME_TOPICS}"

# 8 个 Routine Load 作业名（rl_<表名>）
REALTIME_ROUTINE_LOADS="rl_dwd_trade_order_detail rl_dwd_trade_payment_detail \
rl_dwd_trade_refund_detail rl_dwd_traffic_behavior_detail \
rl_dws_traffic_overview_1m \
rl_ads_realtime_trade_1m rl_ads_realtime_traffic_1m rl_ads_realtime_category_1m"

# 期望同时在运行的 Flink 作业数：
#   4 个 DWD 清洗作业 + 4 个指标窗口作业（见 infrastructure/flink/sql/04、05）
FLINK_EXPECTED_JOBS=8

# 期望同时在运行的 8 个 sink 作业（Flink 作业名 = insert-into_<catalog>.<db>.<表>）
FLINK_EXPECTED_SINKS="sink_dwd_trade_order_detail sink_dwd_trade_payment_detail \
sink_dwd_trade_refund_detail sink_dwd_traffic_behavior_detail \
sink_dws_traffic_overview_1m sink_ads_realtime_trade_1m \
sink_ads_realtime_traffic_1m sink_ads_realtime_category_1m"

is_realtime_deployed() {
    container_running flink-jobmanager
}

# ------------------------------------------------------------
# Flink 集群：JobManager REST /overview
# ------------------------------------------------------------
check_flink_cluster() {
    if ! container_running flink-jobmanager; then
        record "Flink 集群" 0 "容器 flink-jobmanager 未运行"
        return
    fi

    local body
    body="$(docker exec flink-jobmanager curl -fsS --max-time 8 \
        http://localhost:8081/overview 2>/dev/null || true)"

    if [ -z "${body}" ]; then
        record "Flink 集群" 0 "JobManager REST(8081) /overview 无响应"
        return
    fi

    local slots running failed
    slots="$(printf '%s' "${body}"   | grep -o '"slots-total":[0-9]*'    | grep -o '[0-9]*$' || true)"
    running="$(printf '%s' "${body}" | grep -o '"jobs-running":[0-9]*'   | grep -o '[0-9]*$' || true)"
    failed="$(printf '%s' "${body}"  | grep -o '"jobs-failed":[0-9]*'    | grep -o '[0-9]*$' || true)"
    slots="${slots:-0}"; running="${running:-0}"; failed="${failed:-0}"

    if [ "${slots}" -lt 1 ]; then
        record "Flink 集群" 0 "TaskManager 未注册（slots-total=0）"
        return
    fi

    if [ "${running}" -lt "${FLINK_EXPECTED_JOBS}" ]; then
        record "Flink 集群" 0 \
            "slots=${slots}，但仅 ${running}/${FLINK_EXPECTED_JOBS} 个作业在运行（见 docker logs flink-jobs）"
        return
    fi

    # 历史失败作业（开发期反复提交会留下记录）不影响当前健康状态，
    # 但必须如实展示，避免"看起来一切正常"。
    record "Flink 集群" 1 "slots=${slots}，${running} 个作业运行中（历史失败 ${failed} 个）"
}

# ------------------------------------------------------------
# Flink 作业唯一性：8 个 sink 作业各恰好一个处于 RUNNING
#
# 为什么必须单独检查（实测踩坑）：
#   SQL Gateway 是 session 模式 —— 重启 flink-jobs 容器**不会**取消旧作业，
#   旧 session 仍存活并占用 slot。结果是：
#     1) 新作业拿不到资源而失败（NoResourceAvailableException）；
#     2) 旧作业带着旧窗口状态重算同一批事件，指标翻倍
#        （实测 PV 合计 39987，而事件总数只有 20000）。
#   只数 jobs-running 无法发现（1 新 + 1 旧 与 2 个正常作业计数相同）。
# ------------------------------------------------------------
check_flink_jobs_unique() {
    if ! container_running flink-jobmanager; then
        record "Flink 作业唯一性" 0 "容器 flink-jobmanager 未运行"
        return
    fi

    local raw running
    raw="$(docker exec flink-jobmanager curl -fsS --max-time 10 \
        http://localhost:8081/jobs/overview 2>/dev/null || true)"

    if [ -z "${raw}" ]; then
        record "Flink 作业唯一性" 0 "JobManager REST /jobs/overview 无响应"
        return
    fi

    running="$(printf '%s' "${raw}" \
        | sed 's/},{/}\n{/g' \
        | awk '
            {
                state = ""; name = ""
                if (match($0, /"state":"[A-Z_]*"/)) {
                    state = substr($0, RSTART + 9, RLENGTH - 10)
                }
                if (match($0, /"name":"[^"]*"/)) {
                    name = substr($0, RSTART + 8, RLENGTH - 9)
                }
                if (state == "RUNNING" && name != "") print name
            }')"

    local sink cnt missing_names="" dup_names="" total=0
    for sink in ${FLINK_EXPECTED_SINKS}; do
        cnt="$(printf '%s\n' "${running}" | grep -c -- "${sink}" || true)"
        cnt="${cnt:-0}"
        total=$(( total + cnt ))
        if [ "${cnt}" -eq 0 ]; then
            missing_names="${missing_names} ${sink}"
        elif [ "${cnt}" -gt 1 ]; then
            dup_names="${dup_names} ${sink}×${cnt}"
        fi
    done

    if [ -n "${missing_names}" ]; then
        record "Flink 作业唯一性" 0 "缺少运行中的作业:${missing_names}"
        return
    fi
    if [ -n "${dup_names}" ]; then
        record "Flink 作业唯一性" 0 \
            "存在重复作业（旧 session 遗留）:${dup_names} —— 执行 bash scripts/cancel-flink-jobs.sh 后重试"
        return
    fi

    record "Flink 作业唯一性" 1 "8 个 sink 作业各 1 个实例（共 ${total} 个）"
}

# ------------------------------------------------------------
# Flink SQL Gateway：/v1/info
# ------------------------------------------------------------
check_flink_gateway() {
    if ! container_running flink-sql-gateway; then
        record "Flink SQL Gateway" 0 "容器 flink-sql-gateway 未运行"
        return
    fi

    local body
    body="$(docker exec flink-jobmanager curl -fsS --max-time 8 \
        http://flink-sql-gateway:8083/v1/info 2>/dev/null || true)"

    if printf '%s' "${body}" | grep -q '"productName"'; then
        record "Flink SQL Gateway" 1 "REST(8083) 已就绪"
    else
        record "Flink SQL Gateway" 0 "REST(8083) /v1/info 无响应"
    fi
}

# ------------------------------------------------------------
# 实时 Topic：8 个下游 Topic 必须存在
# ------------------------------------------------------------
check_realtime_topics() {
    if ! container_running kafka; then
        record "Kafka 实时 Topic" 0 "容器 kafka 未运行"
        return
    fi

    local topics missing=0
    topics="$(docker exec kafka /opt/kafka/bin/kafka-topics.sh \
        --bootstrap-server kafka:9092 --list 2>/dev/null || true)"

    for t in ${REALTIME_TOPICS}; do
        if ! printf '%s\n' "${topics}" | grep -qx "${t}"; then
            missing=$(( missing + 1 ))
        fi
    done

    if [ "${missing}" -eq 0 ]; then
        record "Kafka 实时 Topic" 1 "8 个实时 Topic 均已创建"
    else
        record "Kafka 实时 Topic" 0 "缺少 ${missing} 个实时 Topic（见 kafka-init 容器日志）"
    fi
}

# ------------------------------------------------------------
# Doris 数仓表：8 张 DWD/DWS/ADS 表必须存在
# ------------------------------------------------------------
check_doris_realtime_tables() {
    local out missing=0
    out="$(docker exec doris-be mysql \
        -h "${DORIS_FE_IP}" -P 9030 -uroot --connect-timeout=5 -N -B \
        -e "SELECT table_name FROM information_schema.tables WHERE table_schema='ecommerce';" \
        2>/dev/null || true)"

    if [ -z "${out}" ]; then
        record "Doris 实时表" 0 "无法查询 information_schema（FE 未就绪？）"
        return
    fi

    for t in ${REALTIME_TABLES}; do
        if ! printf '%s\n' "${out}" | grep -qx "${t}"; then
            missing=$(( missing + 1 ))
        fi
    done

    if [ "${missing}" -eq 0 ]; then
        record "Doris 实时表" 1 "8 张 DWD/DWS/ADS 表均已创建"
    else
        record "Doris 实时表" 0 "缺少 ${missing} 张表（检查 sql/doris/10~12 是否执行）"
    fi
}

# ------------------------------------------------------------
# Routine Load：8 个实时导入作业必须存在且处于 RUNNING
#
# 为什么这是最关键的一项：
#   Topic 里有数据 ≠ Doris 里有数据。Routine Load 一旦 PAUSED，
#   链路就是"看起来在跑、实际断流"，必须显式检查。
# ------------------------------------------------------------
check_routine_load() {
    # 注意：**不能加 -N**！-N 会去掉 "Name:" / "State:" 这些字段标签，
    # 下面的解析就全部失效（实测：作业明明 RUNNING 却被判为"缺少 8 个"）。
    # 这里与 verify-sprint-1.sh 的 doris_show 保持一致，只用 -B 抑制表格边框。
    local out
    out="$(docker exec doris-be mysql \
        -h "${DORIS_FE_IP}" -P 9030 -uroot --connect-timeout=5 -B \
        -e "USE ecommerce; SHOW ROUTINE LOAD\G" 2>/dev/null || true)"

    if [ -z "${out}" ]; then
        record "Doris Routine Load" 0 "无法执行 SHOW ROUTINE LOAD（BE 未注册或 FE 不可用？）"
        return
    fi

    local expected=0 exist=0 running=0 name
    local missing_names="" paused_names=""

    for name in ${REALTIME_ROUTINE_LOADS}; do
        expected=$(( expected + 1 ))
        if printf '%s\n' "${out}" | grep -qE "^[[:space:]]*Name: ${name}[[:space:]]*$"; then
            exist=$(( exist + 1 ))
            # 取该作业块内的 State 行
            if printf '%s\n' "${out}" \
                | awk -v n="${name}" '
                    $1 == "Name:" { cur = $2; next }
                    $1 == "State:" && cur == n { print $2; exit }' \
                | grep -qx "RUNNING"; then
                running=$(( running + 1 ))
            else
                paused_names="${paused_names} ${name}"
            fi
        else
            missing_names="${missing_names} ${name}"
        fi
    done

    if [ "${exist}" -lt "${expected}" ]; then
        record "Doris Routine Load" 0 \
            "缺少 $(( expected - exist )) 个作业:${missing_names}"
        return
    fi

    if [ "${running}" -lt "${expected}" ]; then
        record "Doris Routine Load" 0 \
            "${running}/${expected} 个作业 RUNNING，异常作业:${paused_names}（用 SHOW ROUTINE LOAD 查看 ReasonOfStateChanged）"
        return
    fi

    record "Doris Routine Load" 1 "${running}/${expected} 个作业 RUNNING"
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

    # ---- Sprint 1：实时数仓链路 ----
    if is_realtime_deployed; then
        printf '\n'
        printf '%b\n' "${C_BOLD} Sprint 1 — 实时数仓链路${C_RESET}"
        check_flink_cluster
        check_flink_jobs_unique
        check_flink_gateway
        check_realtime_topics
        check_doris_realtime_tables
        check_routine_load
    else
        printf '\n'
        record_skip "Sprint 1 实时链路（Flink / 实时 Topic / Routine Load）" \
            "容器 flink-jobmanager 未运行 —— 当前环境只部署了 Sprint 0"
    fi

    printf '\n'
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    if [ "${FAILED}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} All services are healthy${C_RESET}"
        printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
        printf '\n'
        if [ "${SKIPPED}" -gt 0 ]; then
            printf '注意：有 %s 组检查被跳过（对应 Sprint 未部署）\n' "${SKIPPED}"
            printf '\n'
        fi
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
    printf '  docker compose logs flink-jobmanager 查看 Flink 集群日志\n'
    printf '  docker logs flink-jobs              查看 Flink SQL 提交结果\n'
    printf '  docker exec doris-be mysql -h %s -P 9030 -uroot \\\n' "${DORIS_FE_IP}"
    printf '      -e "USE ecommerce; SHOW ROUTINE LOAD\\G"   查看实时导入作业\n'
    printf '\n'
    return 1
}

main "$@" < /dev/null
exit $?
