#!/usr/bin/env bash
# ============================================================
# scripts/batch-mode.sh — 错峰批处理（暂停实时链路 → 跑离线 → 恢复实时链路）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/batch-mode.sh                          # 暂停实时链路，跑完整离线流水线，再恢复
#   bash scripts/batch-mode.sh --stage ads              # 只跑一个阶段（可重复）
#   bash scripts/batch-mode.sh --keep-realtime          # 不暂停实时链路（只在内存充裕时用）
#   bash scripts/batch-mode.sh --no-restore             # 跑完不自动恢复实时链路
#   bash scripts/batch-mode.sh --restore-only           # 只恢复实时链路（上次异常中断后用）
#
# 为什么必须"错峰"而不是"挤一挤"：
#   实测（Sprint 3 事故）：16 GB 机器上实时链路常驻约 13.7 GB，空闲约 2.0 GB；
#   离线 Spark 批处理的驱动跑在**宿主机**上（client 模式），加执行器合计约 1.5 GB。
#   两者叠加会把可用内存压到 200 MB 以下，直接后果是：
#     - Doris BE 报 MEM_ALLOC_FAILED，看板与接口不可用；
#     - 宿主机无 swap，内核无法回收，SSH 失联，只能从云控制台强制重启。
#   因此本脚本在跑批前**主动暂停实时链路**，跑完再恢复。
#
# 暂停实时链路会不会丢数据？不会，原因：
#   1) Kafka 是缓冲：Flink 停了，事件继续堆在 topic 里（保留策略见 kafka 配置）；
#   2) Flink 的消费位点在 checkpoint / 已提交 offset 里，
#      重启后从上次位置继续，重复的部分由 Doris 的 UNIQUE KEY 幂等覆盖；
#   3) 唯一影响是"暂停期间看板上的实时曲线会延后追上"，
#      恢复后 scripts/health-check.sh 会验证链路确实回到健康。
#
# 为什么不用 `docker compose stop` 停整个 Flink 而是一个个停：
#   flink-jobs 是一次性容器（提交完作业就退出），
#   停它的意义是避免它在恢复时被 compose 重新拉起两次提交作业。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
# shellcheck source=lib/memory-guard.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/memory-guard.sh"

PIPELINE_ARGS=()
KEEP_REALTIME=0
NO_RESTORE=0
RESTORE_ONLY=0
PAUSE_ONLY=0

usage() {
    cat <<'TXT'
用法： bash scripts/batch-mode.sh [选项] [-- <run-batch-pipeline.sh 的参数>]

选项：
  --stage <name>     只跑指定阶段（ods|dwd|dws|ads|load|reconcile），可重复
  --skip-reconcile   透传给 run-batch-pipeline.sh
  --keep-realtime    不暂停实时链路（仅在内存充裕时使用）
  --no-restore       跑完不自动恢复实时链路
  --pause-only       只暂停实时链路，不跑批（Sprint 4：给 Airflow 用）
  --restore-only     只恢复实时链路，不跑批
  -h, --help         显示本帮助

为什么需要 --pause-only 与 --restore-only 这一对：
  Airflow 的 DAG 要把"暂停实时链路"和"恢复实时链路"做成两个**独立任务**，
  中间夹着逐层的批处理任务。这样做的收益是：
    - 每一层都能单独看到成功/失败并单独重试；
    - 恢复任务可以用 trigger_rule=all_done，保证**任一环节失败也会恢复**
      实时链路 —— 否则一次失败的批处理会让看板一直停在那里，
      那比批处理失败本身严重得多。
  把整个 batch-mode.sh 塞进一个任务就退化成 cron 了，失去上述两点。
TXT
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --keep-realtime) KEEP_REALTIME=1; shift ;;
            --no-restore)    NO_RESTORE=1; shift ;;
            --restore-only)  RESTORE_ONLY=1; shift ;;
            --pause-only)    PAUSE_ONLY=1; shift ;;
            --stage|--skip-reconcile)
                PIPELINE_ARGS+=("$1")
                if [ "$1" = "--stage" ]; then
                    PIPELINE_ARGS+=("${2:-}")
                    shift 2
                else
                    shift
                fi
                ;;
            -h|--help) usage; exit 0 ;;
            *) log_error "未知参数：$1"; usage; exit 2 ;;
        esac
    done
}

pause_realtime() {
    printf '\n%b\n' "${C_BOLD}── 暂停实时链路（为批处理腾出内存）──${C_RESET}"
    local c stopped=0 skipped=0 failed=0
    for c in "${REALTIME_STACK_CONTAINERS[@]}"; do
        if ! container_running "${c}"; then
            printf '  跳过 %s（未运行）\n' "${c}"
            skipped=$(( skipped + 1 ))
            continue
        fi

        # !! 这里绝对不能吞掉 stderr !!
        #   实测踩坑（Sprint 4，Airflow 里跑 DAG 时暴露）：
        #   原实现是 `compose stop "${c}" >/dev/null 2>&1 && { printf '已暂停'; }`。
        #   当 compose stop 失败时 `&&` 短路 —— **既不打印"已暂停"，也不打印错误**，
        #   函数最后照样输出"已暂停 0 个容器"并返回 0。
        #   结果：Airflow 看到任务 success，而实时链路根本没暂停，
        #   紧接着每个 batch stage 都被内存闸门拒绝（可用内存不足），
        #   整条 DAG 全线失败，日志里却看不到任何"暂停失败"的痕迹。
        #
        #   这就是"静默 pass"的典型代价：一个被吞掉的错误，
        #   最终表现为"任务成功但什么都干不了"，排查方向完全被带偏。
        local err
        if err="$(compose stop "${c}" 2>&1)"; then
            printf '  已暂停 %s\n' "${c}"
            stopped=$(( stopped + 1 ))
        else
            printf '  %b 暂停 %s 失败\n' "${C_RED}[FAIL]${C_RESET}" "${c}"
            printf '    %s\n' "${err}"
            failed=$(( failed + 1 ))
        fi
    done
    # 等内存真正释放（容器退出到内存归还有一两秒延迟）
    sleep 5
    printf '  实时链路已暂停 %s 个容器；当前可用内存 %s MB\n' "${stopped}" "$(host_available_mb)"

    # !! "什么都没做"与"做成功了"必须能区分开 !!
    #   全部跳过（本来就都停着）算成功（幂等）；
    #   但只要有失败，就必须返回非 0，让调用方停下来。
    if [ "${failed}" -gt 0 ]; then
        log_error "${failed} 个容器暂停失败，实时链路未完全停止"
        log_error "后续批处理会因内存不足被闸门拒绝，已中止"
        return 1
    fi
    if [ "${stopped}" -eq 0 ] && [ "${skipped}" -eq 0 ]; then
        log_error "没有任何容器被处理（容器清单可能为空）—— 实时链路状态未知"
        return 1
    fi
    return 0
}

restore_realtime() {
    printf '\n%b\n' "${C_BOLD}── 恢复实时链路 ──${C_RESET}"
    if ! compose up -d flink-jobmanager flink-taskmanager 2>&1 | tail -4; then
        log_error "Flink 集群启动失败，请检查： docker compose logs --tail=50 flink-jobmanager"
        return 1
    fi

    local deadline=$(( SECONDS + 120 ))
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        local ok=0
        for c in flink-jobmanager flink-taskmanager; do
            [ "$(docker inspect -f '{{.State.Health.Status}}' "${c}" 2>/dev/null)" = "healthy" ] && ok=$(( ok + 1 ))
        done
        [ "${ok}" -eq 2 ] && break
        printf '\r  等待 Flink 集群就绪 ... %s/2 (%ss)' "${ok}" "${SECONDS}"
        sleep 5
    done
    printf '\r\033[K'

    # SQL Gateway 与作业提交容器随后拉起（它们依赖集群）
    if ! compose up -d flink-sql-gateway flink-jobs 2>&1 | tail -4; then
        log_error "SQL Gateway / 作业提交失败"
        return 1
    fi

    # !! 健康检查要**重试**，还要**自愈** !!
    #
    #   重试的理由（Sprint 4）：Flink 作业从 Kafka 已提交位点续跑、
    #   Routine Load 重新变 RUNNING，有时要 1 分钟以上。
    #   原来 `sleep 25` 只查一次，会出现最糟的失败形态：
    #   **链路其实正在恢复，任务却被判失败** —— 而这时 DAG 已经结束，
    #   没有后续任务再来确认，实时链路就停在一个没人看的状态里。
    #
    #   自愈的理由（Sprint 5 阶段 4 实测）：
    #   有一种情况**怎么等都不会好，因为它不是"慢"，是"死"**：
    #   jobmanager 先起、taskmanager 后起（相差 5 分钟），
    #   `flink-jobs` 容器在资源就绪前就提交了全部 SQL，
    #   部分作业因拿不到资源直接 FAILED —— 而 FAILED 的作业不会自动重试。
    #   此时唯一的出路是"取消 + 重新提交"。
    #   （症状与"还在恢复"完全一样，只有主动做一次才知道是哪种。）
    printf '  等待作业恢复（Flink 从 Kafka 已提交位点继续消费）...\n'
    local hc_deadline=$(( SECONDS + 180 ))
    local hc_ok=0
    local hc_healed=0
    while [ "${SECONDS}" -lt "${hc_deadline}" ]; do
        if bash "${REPO_ROOT}/scripts/health-check.sh" >/dev/null 2>&1; then
            hc_ok=1
            break
        fi

        # 等到一半还不好，就不再干等：取消失败作业并重新提交（每次恢复最多做一次）
        if [ "${hc_healed}" -eq 0 ] && [ "$(( hc_deadline - SECONDS ))" -le 120 ]; then
            hc_healed=1
            printf '\r\033[K  健康检查迟迟不通过 → 取消作业并重新提交（自愈）...\n'
            bash "${REPO_ROOT}/scripts/cancel-flink-jobs.sh" >/dev/null 2>&1 || true
            compose restart flink-jobs >/dev/null 2>&1 || true
            sleep 20
        fi

        printf '\r  健康检查未通过，重试中 ... 剩余 %ss' "$(( hc_deadline - SECONDS ))"
        sleep 15
    done
    printf '\r\033[K'

    printf '\n'
    if [ "${hc_ok}" -eq 1 ]; then
        log_ok "实时链路已恢复并通过健康检查"
        return 0
    fi

    # 超时后把真实输出打出来（重试期间被丢掉了），否则只剩一句"未通过"
    bash "${REPO_ROOT}/scripts/health-check.sh" || true
    log_error "实时链路恢复后健康检查未通过，请人工确认"
    printf '  排查： bash scripts/verify-sprint-1.sh\n'
    return 1
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 错峰批处理（Sprint 3）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    parse_args "$@"
    load_env
    require_docker

    if [ "${RESTORE_ONLY}" -eq 1 ]; then
        restore_realtime
        exit $?
    fi

    if [ "${PAUSE_ONLY}" -eq 1 ]; then
        # 暂停前也过一遍"没有正在跑的 Spark 作业"闸门：
        # 暂停实时链路本身不危险，但若此时已有作业在跑，
        # 说明有人正在手工跑批，两边叠加会打穿内存（Sprint 3 的事故形态）。
        require_no_running_jobs || exit 1
        # 暂停失败必须让调用方知道（Airflow 会据此把任务标成 failed），
        # 不能"没暂停也报成功" —— 见 pause_realtime 里的踩坑说明。
        pause_realtime || {
            log_error "暂停实时链路失败，已中止"
            exit 1
        }
        printf '\n'
        log_ok "实时链路已暂停；跑完批处理后请执行： bash scripts/batch-mode.sh --restore-only"
        exit 0
    fi

    # 闸门：不要叠加第二个 Spark 作业
    require_no_running_jobs || exit 1

    local paused=0
    if [ "${KEEP_REALTIME}" -eq 0 ]; then
        pause_realtime || {
            log_error "暂停实时链路失败，已中止（未跑批）"
            exit 1
        }
        paused=1
    else
        log_warn "按 --keep-realtime 运行：实时链路保持在线，请自行确认内存充裕"
    fi

    # 闸门：内存够不够（暂停后应该轻松达标）
    if ! require_memory_for_batch; then
        if [ "${paused}" -eq 1 ] && [ "${NO_RESTORE}" -eq 0 ]; then
            log_info "内存仍不足，先恢复实时链路"
            restore_realtime || true
        fi
        exit 1
    fi

    printf '\n%b\n' "${C_BOLD}── 执行离线流水线 ──${C_RESET}"
    local rc=0
    bash "${REPO_ROOT}/scripts/run-batch-pipeline.sh" ${PIPELINE_ARGS[@]+"${PIPELINE_ARGS[@]}"} || rc=$?

    if [ "${paused}" -eq 1 ] && [ "${NO_RESTORE}" -eq 0 ]; then
        restore_realtime || true
    fi

    printf '\n'
    if [ "${rc}" -eq 0 ]; then
        log_ok "错峰批处理完成"
    else
        log_error "批处理失败（退出码 ${rc}）"
    fi
    return "${rc}"
}

main "$@" < /dev/null
exit $?
