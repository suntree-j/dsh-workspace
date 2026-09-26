#!/usr/bin/env bash
# ============================================================
# scripts/lib/memory-guard.sh — 宿主机内存闸门（Sprint 3 事故后引入）
# ============================================================
#
# 为什么需要这个文件（一次真实事故）：
#   服务器 16 GB，实时链路（MySQL/Kafka/MinIO/Doris/Flink/Hive/Spark 集群）
#   常驻约 13.7 GB，空闲仅约 2.0 GB。Sprint 3 第一次跑分层批处理时，
#   Spark 驱动（client 模式，跑在宿主机上）与执行器各占约 1 GB，
#   把可用内存压到 157 MB，结果：
#     1) Doris BE 立刻拒绝查询：
#        MEM_ALLOC_FAILED ... sys available memory 157 MB, low water mark 799.46 MB
#        → 看板与接口全部报"数据仓库暂时不可用"；
#     2) 宿主机没有 swap，内核无法回收，SSH 连会话都建立不起来，
#        整台机器失联，只能从云控制台强制重启。
#
#   结论：**在这台机器上，内存不是"注意一下"的问题，而是必须上闸门。**
#   本文件提供两个闸门：
#     require_memory_for_batch —— 跑批前检查，不够就直接拒绝启动
#     require_no_running_jobs  —— 有 Spark 作业在跑时不再叠加第二个
# ============================================================

# 批处理启动前要求的最小可用内存（MB）
#   依据：Spark 驱动 768m + 执行器 768m + 页面缓存与内核余量，
#   实测可用内存低于约 3 GB 时，跑批必然把 Doris 打到 MEM_ALLOC_FAILED。
MIN_AVAILABLE_MB_FOR_BATCH="${MIN_AVAILABLE_MB_FOR_BATCH:-3000}"

# 宿主机可用内存（MB）
host_available_mb() {
    free -m | awk '/^Mem:/ {print $7}'
}

host_total_mb() {
    free -m | awk '/^Mem:/ {print $2}'
}

# 是否有 Spark **作业**正在运行。
#
# !! 为什么不能只看有没有 spark-submit 容器（实测踩坑）!!
#   scripts/spark-sql.sh 也是用一次性 spark-submit 容器跑 `spark-sql -e "..."`。
#   如果把这些临时查询容器当成"作业在运行"，就会出现：
#   验收脚本刚查完湖仓行数、紧接着重跑 ADS 层 → 被闸门拒绝
#   （自己把自己挡住，报"已有 Spark 作业在运行"，而其实一个作业都没有）。
#   因此这里必须区分：
#     作业    : spark-submit ... /opt/jobs/xxx.py
#     临时查询: spark-sql --conf ... -e "SELECT ..."
spark_jobs_running() {
    local pid
    for pid in $(pgrep -f 'org\.apache\.spark\.deploy\.SparkSubmit' 2>/dev/null); do
        # /proc/<pid>/cmdline 以 NUL 分隔；换成空格后判断是否引用了作业脚本
        if tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null | grep -q '/opt/jobs/.*\.py'; then
            return 0
        fi
    done
    return 1
}

# 等待正在运行的 Spark 作业结束（最多 wait_seconds 秒）。
# 返回 0 = 现在没有作业；1 = 超时仍有作业在跑。
wait_for_no_running_jobs() {
    local wait_seconds="${1:-240}"
    local waited=0
    if ! spark_jobs_running; then
        return 0
    fi
    log_warn "检测到 Spark 作业正在运行，等待其结束（最多 ${wait_seconds}s）..."
    while [ "${waited}" -lt "${wait_seconds}" ]; do
        sleep 5
        waited=$(( waited + 5 ))
        if ! spark_jobs_running; then
            printf '\r\033[K'
            printf '  前一个作业已结束（等待 %ss）\n' "${waited}"
            return 0
        fi
        printf '\r  等待中 ... %ss' "${waited}"
    done
    printf '\r\033[K'
    return 1
}

# ------------------------------------------------------------
# 闸门 1：内存是否够跑一批
# ------------------------------------------------------------
require_memory_for_batch() {
    local available total
    available="$(host_available_mb)"
    total="$(host_total_mb)"

    printf '%b\n' "  内存检查： 可用 ${available} MB / 共 ${total} MB（批处理要求 >= ${MIN_AVAILABLE_MB_FOR_BATCH} MB）"

    if [ "${available}" -lt "${MIN_AVAILABLE_MB_FOR_BATCH}" ]; then
        log_error "可用内存不足（${available} MB < ${MIN_AVAILABLE_MB_FOR_BATCH} MB），已拒绝启动批处理"
        printf '  这是有意为之的保护：内存不足时强行跑批会把 Doris 打到 MEM_ALLOC_FAILED，\n'
        printf '  进而让看板与接口不可用（历史事故，见 docs/sprint/SPRINT_3.md 第 8 节）。\n'
        printf '\n可选做法：\n'
        printf '  a) 用 batch 模式跑（自动暂停实时链路，释放约 2.8 GB）：\n'
        printf '       bash scripts/batch-mode.sh --stage <阶段>\n'
        printf '  b) 临时降低阈值（不推荐，仅在确认风险后使用）：\n'
        printf '       MIN_AVAILABLE_MB_FOR_BATCH=2000 bash scripts/run-batch-pipeline.sh\n'
        printf '  c) 先看谁在吃内存：\n'
        printf '       docker stats --no-stream\n'
        return 1
    fi
    return 0
}

# ------------------------------------------------------------
# 闸门 2：不要叠加第二个 Spark 作业
#
# 先等一会儿再判定：上一个作业可能刚刚结束、进程正在退出，
# 立刻判"占用"会让串行流水线在边界上偶发失败（实测踩过一次）。
# ------------------------------------------------------------
require_no_running_jobs() {
    if wait_for_no_running_jobs "${SPARK_WAIT_SECONDS:-240}"; then
        return 0
    fi
    log_error "已有 Spark 作业在运行（等待 ${SPARK_WAIT_SECONDS:-240}s 仍未结束），拒绝再启动一个"
    printf '  原因：两个驱动同时跑会直接打穿这台机器的内存。\n'
    printf '  查看： docker ps --filter name=spark-submit-run\n'
    printf '        pgrep -af SparkSubmit\n'
    printf '  既然流水线本身是串行执行，正常情况下不会走到这里；\n'
    printf '  若确实需要强制重跑，先确认前一作业已结束。\n'
    return 1
}

# ------------------------------------------------------------
# 实时链路（Flink 栈）容器名，供 batch 模式暂停/恢复
# ------------------------------------------------------------
REALTIME_STACK_CONTAINERS=(flink-jobs flink-sql-gateway flink-taskmanager flink-jobmanager)
