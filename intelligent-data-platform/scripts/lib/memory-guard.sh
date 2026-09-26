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

# 是否有 Spark 作业正在运行（排除 Master/Worker 自身的 Java 进程）
spark_jobs_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'spark-submit-run' && return 0
    pgrep -f 'org.apache.spark.deploy.SparkSubmit' >/dev/null 2>&1 && return 0
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
# ------------------------------------------------------------
require_no_running_jobs() {
    if spark_jobs_running; then
        log_error "已有 Spark 作业在运行，拒绝再启动一个"
        printf '  原因：两个驱动同时跑会直接打穿这台机器的内存。\n'
        printf '  查看： docker ps --filter name=spark-submit-run\n'
        printf '  等待： bash scripts/run-batch-pipeline.sh  会串行执行各阶段，无需并发\n'
        return 1
    fi
    return 0
}

# ------------------------------------------------------------
# 实时链路（Flink 栈）容器名，供 batch 模式暂停/恢复
# ------------------------------------------------------------
REALTIME_STACK_CONTAINERS=(flink-jobs flink-sql-gateway flink-taskmanager flink-jobmanager)
