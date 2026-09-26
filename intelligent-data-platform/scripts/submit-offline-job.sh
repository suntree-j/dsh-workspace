#!/usr/bin/env bash
# ============================================================
# scripts/submit-offline-job.sh — 提交离线链路作业（Sprint 2 / Sprint 3）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/submit-offline-job.sh                 # 等价 --stage ods（Sprint 2 的抽取作业）
#   bash scripts/submit-offline-job.sh --stage dwd     # 只跑 ODS → DWD
#   bash scripts/submit-offline-job.sh --stage dws     # 只跑 DWD → DWS
#   bash scripts/submit-offline-job.sh --stage ads     # 只跑 DWD → ADS
#   bash scripts/submit-offline-job.sh --stage reconcile
#
# 与 run-batch-pipeline.sh 的关系：
#   本脚本提交**单个阶段**，适合排查与重跑；
#   run-batch-pipeline.sh 负责按依赖顺序一次跑完（并支持 --stage 只跑一段）。
#   两者共用 scripts/lib/spark-job.sh，凭据注入方式只有一处实现。
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
# shellcheck source=lib/spark-job.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/spark-job.sh"
# shellcheck source=lib/memory-guard.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/memory-guard.sh

STAGE="ods"

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --stage)
                STAGE="${2:-}"
                if [ -z "${STAGE}" ]; then
                    log_error "--stage 缺少参数"
                    exit 2
                fi
                case "${STAGE}" in
                    ods|dwd|dws|ads|reconcile) ;;
                    *)
                        log_error "未知阶段：${STAGE}（可选 ods|dwd|dws|ads|reconcile）"
                        printf '  提示：需要一次跑完请用 bash scripts/run-batch-pipeline.sh\n'
                        exit 2
                        ;;
                esac
                shift 2
                ;;
            -h|--help)
                sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
                exit 0
                ;;
            *)
                log_error "未知参数：$1"
                exit 2
                ;;
        esac
    done
}

STAGE_DESC_ods="MySQL → ODS（Spark JDBC 抽取，作业内逐表对账）"
STAGE_DESC_dwd="ODS → DWD（去重 / 清洗 / 维度补全）"
STAGE_DESC_dws="DWD → DWS（按天轻度聚合）"
STAGE_DESC_ads="DWD → ADS（指标口径，1 分钟 + 1 天）"
STAGE_DESC_reconcile="实时 ADS ↔ 离线 ADS 逐窗口对账"

submit_stage() {
    case "${STAGE}" in
        ods)
            submit_spark_job "sprint2-extract-mysql-to-lakehouse" \
                "infrastructure/spark/jobs/extract_mysql_to_lake.py" \
                --conf "spark.mysql.user=root" \
                --conf "spark.mysql.password=$(_spark_job_env_value MYSQL_ROOT_PASSWORD)" \
                --conf "spark.jobs.ddl=/opt/sql/hive/01_ods_tables.sql"
            ;;
        dwd)
            submit_spark_job "sprint3-build-dwd" "infrastructure/spark/jobs/build_dwd.py"
            ;;
        dws)
            submit_spark_job "sprint3-build-dws" "infrastructure/spark/jobs/build_dws.py"
            ;;
        ads)
            submit_spark_job "sprint3-build-ads" "infrastructure/spark/jobs/build_ads.py"
            ;;
        reconcile)
            submit_spark_job "sprint3-reconcile-batch-realtime" \
                "infrastructure/spark/jobs/reconcile_batch_realtime.py" \
                --conf "spark.doris.host=${DORIS_FE_HOST:-doris-fe}" \
                --conf "spark.doris.queryPort=${DORIS_FE_QUERY_PORT:-9030}" \
                --conf "spark.doris.database=${DORIS_DATABASE:-ecommerce}" \
                --conf "spark.doris.user=${DORIS_READONLY_USER:-agent_ro}" \
                --conf "spark.doris.password=$(_spark_job_env_value API_DORIS_PASSWORD)"
            ;;
    esac
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 提交离线作业${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    parse_args "$@"
    load_env
    require_docker

    printf '阶段： %s\n' "${STAGE}"
    eval "printf '说明： %s\\n' \"\${STAGE_DESC_${STAGE}}\""
    if [ "${STAGE}" = "ods" ]; then
        printf '目标： s3a://lakehouse/warehouse/ods/*  +  Hive 外部表 lakehouse.ods_*（overwrite 幂等）\n'
    else
        printf '目标： 湖仓 lakehouse 库（各层作业自带对账断言，失败即非 0 退出）\n'
    fi
    printf '\n'

    # 内存闸门（见 scripts/lib/memory-guard.sh 的事故说明）
    require_no_running_jobs || exit 1
    require_memory_for_batch || {
        printf '\n  提示：推荐用错峰模式： bash scripts/batch-mode.sh --stage %s\n' "${STAGE}"
        exit 1
    }

    submit_stage
    local rc=$?

    printf '\n'
    if [ "${rc}" -eq 0 ]; then
        log_ok "阶段 ${STAGE} 执行成功"
        printf '  查看结果： bash scripts/spark-sql.sh -e "SHOW TABLES IN lakehouse;"\n'
    else
        log_error "阶段 ${STAGE} 失败（退出码 ${rc}）"
        printf '  排查： docker compose logs --tail=80 spark-worker\n'
        printf '        docker exec spark-master ls -l /opt/spark/logs/ 2>/dev/null | tail\n'
    fi
    return "${rc}"
}

main "$@" < /dev/null
exit $?
