#!/usr/bin/env bash
# ============================================================
# scripts/cancel-flink-jobs.sh — 取消全部 Flink 作业并关闭 SQL Gateway 会话
# ============================================================
#
# 用法（在仓库根目录）：
#   bash scripts/cancel-flink-jobs.sh
#
# ------------------------------------------------------------
# 为什么必须有这个脚本（实测踩坑，很隐蔽）
# ------------------------------------------------------------
#   Flink SQL Gateway 是 **session 模式**：作业的生命周期绑定在 session 上。
#   因此「重启 flink-jobs 容器」**不会**取消它提交过的作业 ——
#   旧 session 仍在 Gateway 内存活，旧作业继续占用 TaskManager slot。
#
#   后果有两种，且都很容易被误判为"链路正常"：
#     1) 新提交的作业拿不到 slot，直接失败：
#          NoResourceAvailableException: Could not acquire the minimum required resources
#     2) 更隐蔽：旧作业的窗口状态还在，重放同一批事件时
#        旧作业把新事件**累加到旧窗口**上，导致指标翻倍
#        （实测 PV 合计 39987，而事件总数只有 20000）。
#
#   所以：**重新提交作业前，必须先执行本脚本。**
#   `scripts/verify-sprint-1.sh --replay` 已自动调用它。
#
# 实现说明：
#   先删 session（Gateway 会连带取消该 session 的作业），
#   再兜底取消所有仍处于 RUNNING / RESTARTING / CREATED 的作业。
#   全部通过 docker exec 在容器内执行，不依赖宿主机工具。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

GATEWAY="http://flink-sql-gateway:8083"
JOBMANAGER="http://localhost:8081"

# 在 flink-jobmanager 容器内执行 curl（该镜像自带 curl）
gw() {
    docker exec flink-jobmanager curl -fsS --max-time 10 "$@" 2>/dev/null || true
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 取消 Flink 作业与会话${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'

    load_env
    require_docker

    if ! container_running flink-jobmanager; then
        log_warn "flink-jobmanager 未运行，无需清理"
        return 0
    fi

    # --------------------------------------------------------
    # 1. 关闭所有 SQL Gateway session
    # --------------------------------------------------------
    local sessions handle count=0
    sessions="$(gw "${GATEWAY}/v1/sessions" | grep -o '"[0-9a-f]\{8\}-[0-9a-f-]\{27\}"' | tr -d '"' || true)"

    for handle in ${sessions}; do
        gw -X DELETE "${GATEWAY}/v1/sessions/${handle}" >/dev/null
        count=$(( count + 1 ))
    done
    log_info "已关闭 SQL Gateway session：${count} 个"

    # --------------------------------------------------------
    # 2. 兜底：取消仍在运行的作业
    # --------------------------------------------------------
    local body jobs_left=0 cancelled=0
    body="$(gw "${JOBMANAGER}/jobs")"

    # {"jobs":[{"id":"...","status":"RUNNING"},...]}
    local entry jid state
    while IFS= read -r entry; do
        [ -n "${entry}" ] || continue
        jid="$(printf '%s' "${entry}" | grep -o '"id":"[0-9a-f]\{32\}"' | head -1 | cut -d'"' -f4)"
        state="$(printf '%s' "${entry}" | grep -o '"status":"[A-Z_]*"' | head -1 | cut -d'"' -f4)"
        [ -n "${jid}" ] || continue
        case "${state}" in
            RUNNING|RESTARTING|CREATED|RECONCILING|INITIALIZING|CANCELLING)
                gw -X PATCH "${JOBMANAGER}/jobs/${jid}?mode=cancel" >/dev/null
                cancelled=$(( cancelled + 1 ))
                ;;
        esac
    done < <(printf '%s' "${body}" | sed 's/},{/}\n{/g')

    log_info "已取消运行中的作业：${cancelled} 个"

    # --------------------------------------------------------
    # 3. 校验：不应再有非终态作业
    # --------------------------------------------------------
    sleep 3
    body="$(gw "${JOBMANAGER}/jobs")"
    jobs_left="$(printf '%s' "${body}" \
        | sed 's/},{/}\n{/g' \
        | grep -cE '"status":"(RUNNING|RESTARTING|CREATED|RECONCILING|INITIALIZING|CANCELLING)"' || true)"
    jobs_left="${jobs_left:-0}"

    printf '\n'
    if [ "${jobs_left}" -eq 0 ]; then
        log_ok "Flink 集群已无运行中作业，可以重新提交"
        return 0
    fi

    log_warn "仍有 ${jobs_left} 个作业处于非终态，请稍后重试或检查 JobManager 日志"
    return 1
}

main "$@" < /dev/null
exit $?
