#!/usr/bin/env bash
# ============================================================
# scripts/submit-offline-job.sh — 提交离线抽取作业（Sprint 2）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/submit-offline-job.sh            # 全量抽取 + 对账
#
# 做什么：
#   docker compose run --rm spark-submit ... spark-submit \
#       --master spark://spark-master:7077 \
#       /opt/jobs/extract_mysql_to_lake.py
#
# 为什么用 `docker compose run --rm` 而不是常驻容器：
#   Spark 作业是一次性批处理，跑完即退出；常驻只会白占 1 GB 驱动内存。
#   profile=tools 保证 `docker compose up -d` 不会带上它。
#
# 为什么凭据在命令行里传：
#   spark-defaults.conf 不做 ${env} 替换，凭据只能通过 --conf 传；
#   这些参数只出现在**服务器上的进程参数**里，仓库与镜像里没有任何口令。
#   为减少暴露面，脚本执行完不打印包含口令的完整命令。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

compose() {
    docker compose -f "${REPO_ROOT}/docker-compose.yml" --project-directory "${REPO_ROOT}" "$@"
}

require_value() {
    local name="$1"
    local value
    value="$(grep -E "^${name}=" "${REPO_ROOT}/.env" | head -1 | cut -d= -f2- || true)"
    if [ -z "${value}" ]; then
        log_error ".env 缺少 ${name}"
        exit 1
    fi
    printf '%s' "${value}"
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 提交离线抽取作业（Sprint 2）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'

    load_env
    require_docker

    # 前置检查：集群与会话就绪
    for c in spark-master spark-worker hive-metastore; do
        if ! container_running "${c}"; then
            log_error "容器 ${c} 未运行；请先执行 bash scripts/init-lakehouse.sh"
            exit 1
        fi
    done

    local s3_endpoint s3_key s3_secret mysql_pw
    s3_key="$(require_value MINIO_ROOT_USER)"
    s3_secret="$(require_value MINIO_ROOT_PASSWORD)"
    mysql_pw="$(require_value MYSQL_ROOT_PASSWORD)"
    s3_endpoint="${S3_ENDPOINT_INTERNAL:-http://minio:9000}"

    log_info "提交作业 extract_mysql_to_lake.py（driver 在 spark-submit 容器内）"
    log_info "目标：s3a://lakehouse/warehouse/ods/*  +  Hive 外部表 lakehouse.ods_*"
    printf '\n'

    # 说明：--conf 里带口令，因此不 echo 完整命令、不写日志文件
    compose run --rm -T spark-submit \
        /opt/spark/bin/spark-submit \
        --master spark://spark-master:7077 \
        --deploy-mode client \
        --name sprint2-extract-mysql-to-lakehouse \
        --conf "spark.hadoop.fs.s3a.endpoint=${s3_endpoint}" \
        --conf "spark.hadoop.fs.s3a.access.key=${s3_key}" \
        --conf "spark.hadoop.fs.s3a.secret.key=${s3_secret}" \
        --conf "spark.mysql.user=root" \
        --conf "spark.mysql.password=${mysql_pw}" \
        --conf "spark.jobs.ddl=/opt/sql/hive/01_ods_tables.sql" \
        /opt/jobs/extract_mysql_to_lake.py
    local rc=$?

    printf '\n'
    if [ "${rc}" -eq 0 ]; then
        log_ok "作业执行成功（逐表对账见上方输出）"
        printf '  查看结果： docker exec spark-master /opt/spark/bin/spark-sql -e "SELECT COUNT(*) FROM lakehouse.ods_orders;"\n'
    else
        log_error "作业失败（退出码 ${rc}）"
        printf '  排查： docker compose logs --tail=80 spark-worker\n'
        printf '        docker exec spark-master ls -l /opt/spark/logs/\n'
    fi
    return "${rc}"
}

main "$@" < /dev/null
exit $?
