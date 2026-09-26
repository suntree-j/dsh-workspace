#!/usr/bin/env bash
# ============================================================
# scripts/spark-sql.sh — 用一次性容器执行 Spark SQL（离线链路的查询入口）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/spark-sql.sh -e "SHOW TABLES IN lakehouse"
#   bash scripts/spark-sql.sh -e "SELECT COUNT(*) FROM lakehouse.ods_orders"
#   bash scripts/spark-sql.sh -f /opt/sql/hive/01_ods_tables.sql
#
# 为什么用一次性容器，而不是进 spark-master 里跑：
#   spark-sql 会在当前进程里起一个 **driver**（默认 1 GB 堆）。
#   塞进 spark-master 容器会顶破它的内存上限（mem_limit 1 GB），
#   把常驻的 Master 拖挂；一次性容器用完即走，互不影响。
#
# 为什么凭据要在这里注入：
#   spark-defaults.conf 不做 ${env} 替换（那是 Hadoop XML 的能力），
#   S3A 凭据只能通过 --conf 传；口令来自 .env，仓库里没有硬编码。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SPARK_IMAGE="${SPARK_IMAGE:-data-platform/spark:3.5.7}"

main() {
    load_env
    require_docker

    local s3_key s3_secret s3_endpoint
    s3_key="$(grep -E '^MINIO_ROOT_USER=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    s3_secret="$(grep -E '^MINIO_ROOT_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    s3_endpoint="${S3_ENDPOINT_INTERNAL:-http://minio:9000}"

    if [ -z "${s3_key}" ] || [ -z "${s3_secret}" ]; then
        log_error ".env 缺少 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD"
        exit 1
    fi

    if [ "$#" -eq 0 ]; then
        log_error "用法： bash scripts/spark-sql.sh -e \"SELECT 1\"   或   -f <sql 文件>"
        exit 2
    fi

    docker run --rm -i --network data-platform \
        -v "${REPO_ROOT}/infrastructure/spark/jobs:/opt/jobs:ro" \
        -v "${REPO_ROOT}/sql/hive:/opt/sql/hive:ro" \
        -v "${REPO_ROOT}/infrastructure/spark/conf/spark-defaults.conf:/opt/spark/conf/spark-defaults.conf:ro" \
        -e TZ="${TZ:-Asia/Shanghai}" \
        "${SPARK_IMAGE}" \
        /opt/spark/bin/spark-sql \
        --conf "spark.hadoop.fs.s3a.endpoint=${s3_endpoint}" \
        --conf "spark.hadoop.fs.s3a.access.key=${s3_key}" \
        --conf "spark.hadoop.fs.s3a.secret.key=${s3_secret}" \
        "$@"
}

main "$@" < /dev/null
exit $?
