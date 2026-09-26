#!/usr/bin/env bash
# ============================================================
# scripts/lib/spark-job.sh — Spark 作业提交的公共封装（Sprint 3 引入）
# ============================================================
#
# 为什么单独抽出来：
#   Sprint 2 只有 1 个 Spark 作业，提交逻辑内联在 submit-offline-job.sh 里；
#   Sprint 3 变成 5 个阶段（抽取 / DWD / DWS / ADS / 对账），
#   如果每个脚本各写一份 spark-submit 参数，凭据注入方式一旦要改
#   （例如换连接账号），就得改 5 处，很容易漏掉一处导致"某个阶段连不上库"。
#
# 调用约定：
#   submit_spark_job <app-name> <job 相对路径> [额外的 --conf 参数...]
#
# 依赖调用方先执行 load_env（来自 scripts/lib/common.sh）。
# ============================================================

# 内部：从 .env 取值，缺失即报错退出（绝不使用空口令继续跑）
_spark_job_env_value() {
    local key="$1"
    local value
    value="$(grep -E "^${key}=" "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    if [ -z "${value}" ]; then
        log_error ".env 缺少 ${key}（请对照 .env.example 补齐）"
        exit 1
    fi
    printf '%s' "${value}"
}

# 内部：等待 Spark 集群可用
_spark_job_require_cluster() {
    if ! container_running spark-master || ! container_running spark-worker; then
        log_error "Spark 集群未运行；请先执行 bash scripts/init-lakehouse.sh"
        exit 1
    fi
    if ! container_running hive-metastore; then
        log_error "Hive Metastore 未运行；请先执行 bash scripts/init-lakehouse.sh"
        exit 1
    fi
}

# 提交一个 Spark 作业（driver 跑在一次性容器里）
#
# 参数：
#   $1 作业名（--name）
#   $2 作业脚本路径（相对仓库根，例如 infrastructure/spark/jobs/build_dwd.py）
#   $3.. 额外的 spark-submit --conf 参数（可选）
submit_spark_job() {
    local app_name="$1"; shift
    local job_rel="$1"; shift

    if [ ! -f "${REPO_ROOT}/${job_rel}" ]; then
        log_error "作业脚本不存在：${job_rel}"
        exit 1
    fi

    _spark_job_require_cluster

    local s3_endpoint s3_key s3_secret
    s3_endpoint="${S3_ENDPOINT_INTERNAL:-http://minio:9000}"
    s3_key="$(_spark_job_env_value MINIO_ROOT_USER)"
    s3_secret="$(_spark_job_env_value MINIO_ROOT_PASSWORD)"

    # 说明：--conf 里带口令，因此不 echo 完整命令、不写日志文件
    #
    # !! 内存上限（Sprint 3 实测踩坑，必须显式给）!!
    #   服务器 16 GB 上已常驻 MySQL/Kafka/MinIO/Doris/Flink/Hive/Spark 集群，
    #   空闲内存只有约 2 GB。Spark 驱动在**宿主机**上跑（client 模式），
    #   默认 1 GB 堆；再加一个 1 GB 的执行器，可用内存会被压到 200 MB 以下，
    #   直接后果（真实事故）：
    #     - Doris BE 查询报
    #       MEM_ALLOC_FAILED ... sys available memory 157 MB, low water mark 799.46 MB
    #       → 看板与接口全部报"数据仓库暂时不可用"；
    #     - 宿主机没有 swap，内核无处回收，SSH 无法建立会话，整机失联，
    #       只能从云控制台强制重启（详见 docs/sprint/SPRINT_3.md 第 8 节）。
    #   因此批处理作业的驱动/执行器各自压到 768m，
    #   并允许用环境变量临时放大（数据量变大时再调，而不是改脚本默认值）。
    #
    #   真正的防护不在这里，而在两道闸门：
    #     scripts/lib/memory-guard.sh  —— 可用内存不足直接拒绝启动
    #     scripts/batch-mode.sh        —— 跑批前暂停实时链路，错峰使用内存
    local driver_mem executor_mem max_result
    driver_mem="${OFFLINE_SPARK_DRIVER_MEMORY:-768m}"
    executor_mem="${OFFLINE_SPARK_EXECUTOR_MEMORY:-768m}"
    max_result="${OFFLINE_SPARK_MAX_RESULT_SIZE:-256m}"

    compose run --rm -T spark-submit \
        /opt/spark/bin/spark-submit \
        --master spark://spark-master:7077 \
        --deploy-mode client \
        --name "${app_name}" \
        --driver-memory "${driver_mem}" \
        --executor-memory "${executor_mem}" \
        --conf "spark.driver.maxResultSize=${max_result}" \
        --conf "spark.hadoop.fs.s3a.endpoint=${s3_endpoint}" \
        --conf "spark.hadoop.fs.s3a.access.key=${s3_key}" \
        --conf "spark.hadoop.fs.s3a.secret.key=${s3_secret}" \
        --conf "spark.jobs.sqlDir=/opt/sql/hive" \
        --conf "spark.jobs.layerSqlDir=/opt/layer-sql" \
        "$@" \
        "/opt/jobs/${job_rel##*/}"
}
