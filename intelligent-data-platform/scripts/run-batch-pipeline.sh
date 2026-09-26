#!/usr/bin/env bash
# ============================================================
# scripts/run-batch-pipeline.sh — 离线批处理流水线（Sprint 3）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/run-batch-pipeline.sh                 # 全链路：抽取 → DWD → DWS → ADS → 对账 → 装载
#   bash scripts/run-batch-pipeline.sh --stage dwd     # 只跑某一层
#   bash scripts/run-batch-pipeline.sh --skip-reconcile
#   bash scripts/run-batch-pipeline.sh --list
#
# 阶段依赖：
#   ods   MySQL  → ODS（Sprint 2 的抽取作业）
#   archive       Kafka → ODS（Sprint 4 新增：行为事件归档，补齐流量域离线源）
#   dwd   ODS    → DWD   去重 / 清洗 / 维度补全
#   dws   DWD    → DWS   按天轻度聚合
#   ads   DWD    → ADS   指标口径（含 1 分钟粒度）
#   reconcile    实时 ADS ↔ 离线 ADS 逐窗口比对，产出对账结果表
#   load  ADS/对账结果 → Doris 只读服务用的表（S3() TVF 直读，无需额外 loader）
#
# !! 为什么 reconcile 必须排在 load 前面（实测踩坑）!!
#   load 要装载的 6 张表里包含 ads_reconcile_trade_1m / ads_reconcile_summary，
#   这两张是**对账阶段的产物**。曾经把 load 放在 reconcile 之前，
#   结果前 4 张表装载成功、第 5 张报"装载失败"——
#   因为它的 Parquet 目录此时还不存在（S3() TVF 匹配不到文件，静默返回空）。
#   顺序即依赖：先算出来，再搬进服务库。
#
# !! 为什么 archive 排在 ods 之后、dwd 之前 !!
#   ods 与 archive 是**两个互不依赖的取数源**（MySQL 业务库 / Kafka 事件流），
#   理论上可并行；这里串行是为了内存 —— 本机可用内存只够一次一个 Spark 作业。
#   两者都必须在 dwd 之前：dwd 要同时读交易域与流量域的 ODS。
STAGES=(ods archive dwd dws ads reconcile load iceberg-migrate)

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
# shellcheck source=lib/spark-job.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/spark-job.sh"
# shellcheck source=lib/memory-guard.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/memory-guard.sh"

STAGES=(ods archive dwd dws ads reconcile load iceberg-migrate)
SELECTED=()
SKIP_RECONCILE=0

usage() {
    cat <<'TXT'
用法： bash scripts/run-batch-pipeline.sh [选项]

选项：
  --stage <name>      只跑指定阶段（可重复）；name ∈ ods|archive|dwd|dws|ads|load|reconcile
  --skip-reconcile    全链路模式下跳过对账阶段（离线链路自身仍会自检）
  --list              打印阶段与依赖，然后退出
  -h, --help          显示本帮助
TXT
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --stage)
                local name="${2:-}"
                if [ -z "${name}" ]; then
                    log_error "--stage 缺少参数"
                    exit 2
                fi
                if ! printf '%s\n' "${STAGES[@]}" | grep -qx "${name}"; then
                    log_error "未知阶段：${name}（可选：${STAGES[*]}）"
                    exit 2
                fi
                SELECTED+=("${name}")
                shift 2
                ;;
            --skip-reconcile)
                SKIP_RECONCILE=1
                shift
                ;;
            --list)
                printf '阶段顺序与依赖：\n'
                printf '  %-15s %s\n' ods "MySQL → ODS（Spark JDBC 抽取，作业内逐表对账）"
                printf '  %-15s %s\n' archive "Kafka 行为事件 → ODS（Sprint 4：补齐流量域离线源）"
                printf '  %-15s %s\n' dwd "ODS → DWD（去重 / 清洗 / 维度补全）"
                printf '  %-15s %s\n' dws "DWD → DWS（按天轻度聚合，只出可加指标）"
                printf '  %-15s %s\n' ads "DWD → ADS（指标口径，1 分钟 + 1 天）"
                printf '  %-15s %s\n' reconcile "实时 ADS ↔ 离线 ADS 逐窗口对账（差异不为 0 即失败）"
                printf '  %-15s %s\n' load "ADS + 对账结果 → Doris（S3() TVF 直读 Parquet，供只读服务查询）"
                printf '  %-15s %s\n' iceberg-migrate "Parquet → Iceberg 表格式迁移（Sprint 5，最后执行）"
                printf '\n注意：reconcile 必须在 load 之前 —— load 要装载的表里包含对账结果表。\n'
                printf '注意：iceberg-migrate 必须在最后 —— 它迁移的是"这一轮已经建完整"的湖仓，\n'
                printf '      提前跑会把上一轮的 DWD/DWS/ADS 快照迁过去，得到一个半新半旧的 Iceberg 库。\n'
                exit 0
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                log_error "未知参数：$1"
                usage
                exit 2
                ;;
        esac
    done
}

run_stage() {
    local stage="$1"
    local rc=0
    printf '\n'
    printf '%b\n' "${C_BOLD}──────── 阶段：${stage} ────────${C_RESET}"
    case "${stage}" in
        ods)
            submit_spark_job "sprint2-extract-mysql-to-lakehouse" \
                "infrastructure/spark/jobs/extract_mysql_to_lake.py" \
                --conf "spark.mysql.user=root" \
                --conf "spark.mysql.password=$(_spark_job_env_value MYSQL_ROOT_PASSWORD)" \
                --conf "spark.jobs.ddl=/opt/sql/hive/01_ods_tables.sql"
            rc=$?
            ;;
        archive)
            # Kafka 行为事件 → 湖仓 ODS（Sprint 4：补齐流量域离线源）
            #
            # !! Kafka 地址必须用容器服务名 !!
            #   作业跑在 spark-submit 容器里，用 localhost:19092 会连到容器自己
            #   （AGENTS.md 6.2：容器间禁止 localhost）。
            #   .env 里的 KAFKA_BOOTSTRAP_SERVERS 是给宿主机 Python 用的。
            submit_spark_job "sprint4-archive-behavior-events" \
                "infrastructure/spark/jobs/archive_behavior_events.py" \
                --conf "spark.jobs.ddl=/opt/sql/hive/01_ods_tables.sql" \
                --conf "spark.jobs.kafkaBootstrap=${KAFKA_CONTAINER_BOOTSTRAP:-kafka:9092}"
            rc=$?
            ;;
        dwd)
            submit_spark_job "sprint3-build-dwd" "infrastructure/spark/jobs/build_dwd.py"
            rc=$?
            ;;
        dws)
            submit_spark_job "sprint3-build-dws" "infrastructure/spark/jobs/build_dws.py"
            rc=$?
            ;;
        ads)
            submit_spark_job "sprint3-build-ads" "infrastructure/spark/jobs/build_ads.py"
            rc=$?
            ;;
        load)
            bash "${REPO_ROOT}/scripts/load-batch-to-doris.sh"
            rc=$?
            ;;
        reconcile)
            submit_spark_job "sprint3-reconcile-batch-realtime" \
                "infrastructure/spark/jobs/reconcile_batch_realtime.py" \
                --conf "spark.doris.host=${DORIS_FE_HOST:-doris-fe}" \
                --conf "spark.doris.queryPort=${DORIS_FE_QUERY_PORT:-9030}" \
                --conf "spark.doris.database=${DORIS_DATABASE:-ecommerce}" \
                --conf "spark.doris.user=${DORIS_READONLY_USER:-agent_ro}" \
                --conf "spark.doris.password=$(_spark_job_env_value API_DORIS_PASSWORD)"
            rc=$?
            ;;
        iceberg-migrate)
            # Parquet → Iceberg（Sprint 5）
            #
            # 为什么放在阶段序列的**最后**：
            #   它迁移的是"这一轮已经建完整"的湖仓（ODS/DWD/DWS/ADS 全部落盘）。
            #   插在中间会把上一轮的 DWD/DWS/ADS 快照迁过去，
            #   得到一个半新半旧的 Iceberg 库 —— 而且行数核对还会通过，
            #   因为两边各自都自洽。这类"检查不出错"的错误最难查。
            #
            # 为什么目标库是 lakehouse_iceberg 而不是原地换格式：
            #   Parquet 库与 Iceberg 库**并存**，迁移结果可以逐表核对、可以回滚，
            #   迁移失败也不影响已经在跑的实时链路与数据服务。
            submit_spark_job "sprint5-migrate-parquet-to-iceberg" \
                "infrastructure/spark/jobs/migrate_parquet_to_iceberg.py"
            rc=$?
            ;;
        # !! 兜底分支必须有，而且必须**失败** !!
        #
        #   实测踩坑（Sprint 4）：`archive` 已经加进了 STAGES 数组与 --list 输出，
        #   却漏加了这个 case 的分支。结果 case 直接穿透、rc 保持 0，
        #   脚本打印「阶段 archive 完成」并返回成功 ——
        #   **一个什么都没做的阶段，报告成功了**。
        #   更糟的是 Airflow 也据此把任务标成 success，
        #   于是"流量域归档"这件事看起来做完了，实际 ODS 表里 0 行。
        #
        #   没有兜底的 case 就是一个静默的 no-op 制造机。
        #   现在任何未登记的阶段都会立刻大声失败。
        *)
            log_error "阶段 ${stage} 没有对应的执行分支（run_stage 的 case 缺项）"
            log_error "  这是一个脚本缺陷，不是运行环境问题："
            log_error "  它在 STAGES 数组里，却没有在这里实现，于是会静默地什么都不做。"
            printf '  已实现的阶段： ods archive dwd dws ads reconcile load iceberg-migrate\n'
            rc=1
            ;;
    esac
    return "${rc}"
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 离线批处理流水线（Sprint 3）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    parse_args "$@"
    load_env
    require_docker

    local plan=()
    if [ "${#SELECTED[@]}" -gt 0 ]; then
        plan=("${SELECTED[@]}")
    else
        for stage in "${STAGES[@]}"; do
            if [ "${stage}" = "reconcile" ] && [ "${SKIP_RECONCILE}" -eq 1 ]; then
                continue
            fi
            plan+=("${stage}")
        done
    fi

    printf '执行计划： %s\n' "${plan[*]}"
    printf '凭据来源： .env（不落盘、不进日志）\n'

    # 内存闸门：这台机器上"内存不足还硬跑"会打穿 Doris 并让宿主机失联（见 SPRINT_3.md 第 8 节）。
    # 因此在启动任何 Spark 作业之前先拒绝，而不是等它跑到一半把库打挂。
    if ! require_no_running_jobs; then
        exit 1
    fi
    if ! require_memory_for_batch; then
        printf '\n  提示：推荐用错峰模式（自动暂停实时链路后跑批）：\n'
        printf '        bash scripts/batch-mode.sh %s\n' "${PIPELINE_ARGS_HINT:-}"
        exit 1
    fi

    local done_stages=()
    local stage rc=0
    for stage in "${plan[@]}"; do
        run_stage "${stage}"
        rc=$?
        if [ "${rc}" -ne 0 ]; then
            printf '\n'
            log_error "阶段 ${stage} 失败（退出码 ${rc}），已停止后续阶段"
            printf '  已成功完成： %s\n' "${done_stages[*]:-（无）}"
            printf '  排查建议：\n'
            printf '    docker compose logs --tail=80 spark-worker\n'
            printf '    docker exec spark-master ls -l /opt/spark/logs/ 2>/dev/null | tail\n'
            exit "${rc}"
        fi
        done_stages+=("${stage}")
        log_ok "阶段 ${stage} 完成"
    done

    printf '\n'
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    log_ok "流水线执行完成：${done_stages[*]}"
    printf '  离线表查看： bash scripts/spark-sql.sh -e "SHOW TABLES IN lakehouse;"\n'
    printf '  对账结论：   bash scripts/spark-sql.sh -e "SELECT * FROM lakehouse.ads_reconcile_summary;"\n'
    printf '  全量验收：   bash scripts/verify-sprint-3.sh\n'
}

main "$@" < /dev/null
exit $?
