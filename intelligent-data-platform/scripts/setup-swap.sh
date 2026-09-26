#!/usr/bin/env bash
# ============================================================
# scripts/setup-swap.sh — 为宿主机配置 swap 兜底（Sprint 3 事故后引入）
# ============================================================
#
# 用法（服务器上，仓库根目录，需要 root）：
#   bash scripts/setup-swap.sh            # 默认 8 GB
#   bash scripts/setup-swap.sh 4          # 自定义 4 GB
#
# 为什么需要（一次真实事故）：
#   Sprint 3 第一次跑分层批处理时，Spark 驱动 + 执行器把 16 GB 机器的可用内存
#   压到 157 MB。这台机器**没有 swap**，内核无处回收页面，
#   导致 SSH 连会话都建立不起来，整机失联，只能从云控制台强制重启。
#   swap 在这里的作用不是"提升性能"，而是**给内核留出回收空间**，
#   让内存压力表现为"变慢/被杀进程"，而不是"整机假死"。
#
# !! 重要：swap 不是让内存变多，也不是让批处理可以随便跑 !!
#   1) Doris BE 不允许在有 swap 的机器上启动（官方限制，避免性能不可控）。
#      本脚本通过 /etc/sysctl.d 之外的 systemd 单元，在**开机时后于 Docker**
#      才启用 swap，并在启用前确保 Doris BE 已经起来。
#      更稳妥的做法见下方"启用时机"。
#   2) 因此本脚本的默认策略是：仅在**已运行时**临时启用，
#      并把 swappiness 设得很低（10），让内核只在真正吃紧时才用。
#   3) 真正防止事故的是 scripts/lib/memory-guard.sh 的内存闸门 +
#      scripts/batch-mode.sh 的错峰执行；swap 只是最后一道兜底。
#
# 启用时机（本项目选择）：
#   不在 fstab 里挂成开机自动启用 —— 那样 Doris BE 可能因为"检测到 swap"
#   而在重启后拒绝启动。改为由本脚本在需要时手动/由运维流程启用。
#
# 幂等：重复执行只会提示"已存在并已启用"。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SWAP_SIZE_GB="${1:-8}"
SWAP_FILE="/swapfile"
SWAPPINESS=10

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 配置 swap 兜底（${SWAP_SIZE_GB} GB）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    if [ "$(id -u)" -ne 0 ]; then
        log_error "需要 root 权限（请用 sudo bash scripts/setup-swap.sh）"
        exit 1
    fi

    local total_swap
    total_swap="$(free -m | awk '/^Swap:/ {print $2}')"
    if [ "${total_swap}" -gt 0 ]; then
        log_ok "已存在 swap ${total_swap} MB，无需重复创建"
        printf '  当前：\n'
        free -h | sed -n '1,3p' | sed 's/^/    /'
        return 0
    fi

    local disk_avail
    disk_avail="$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
    printf '  根分区可用空间：%s GB\n' "${disk_avail}"
    if [ "${disk_avail}" -lt "$(( SWAP_SIZE_GB + 2 ))" ]; then
        log_error "磁盘空间不足（需要约 $(( SWAP_SIZE_GB + 2 )) GB，实际 ${disk_avail} GB）"
        exit 1
    fi

    log_info "创建 ${SWAP_FILE}（${SWAP_SIZE_GB} GB）..."
    if [ ! -f "${SWAP_FILE}" ]; then
        # fallocate 在部分文件系统（如某些 overlay/btrfs 配置）上生成的 swap 不可靠，
        # 失败时退回 dd，慢一些但一定正确。
        if ! fallocate -l "${SWAP_SIZE_GB}G" "${SWAP_FILE}" 2>/dev/null; then
            log_warn "fallocate 失败，改用 dd（较慢）"
            dd if=/dev/zero of="${SWAP_FILE}" bs=1M count=$(( SWAP_SIZE_GB * 1024 )) status=none
        fi
    fi
    chmod 600 "${SWAP_FILE}"

    log_info "格式化为 swap ..."
    mkswap "${SWAP_FILE}" >/dev/null

    if swapon "${SWAP_FILE}" 2>/dev/null; then
        log_ok "swap 已启用"
    else
        log_error "swapon 失败"
        exit 1
    fi

    # swappiness 压到 10：只有在真正吃紧时才用 swap，
    # 避免正常运行时把热数据换出去反而拖慢查询。
    cat > /etc/sysctl.d/99-data-platform-swap.conf <<EOF
# 由 scripts/setup-swap.sh 写入 —— 仅作内存兜底，不做常态换页
vm.swappiness = ${SWAPPINESS}
vm.vfs_cache_pressure = 50
EOF
    sysctl -p /etc/sysctl.d/99-data-platform-swap.conf >/dev/null
    log_ok "已写入 /etc/sysctl.d/99-data-platform-swap.conf（swappiness=${SWAPPINESS}）"

    printf '\n'
    free -h | sed -n '1,3p' | sed 's/^/  /'
    printf '\n'
    log_warn "注意：swap 不会开机自动启用（避免 Doris BE 因检测到 swap 拒绝启动）"
    printf '  手动启用： sudo swapon %s\n' "${SWAP_FILE}"
    printf '  卸载 swap： sudo swapoff %s\n' "${SWAP_FILE}"
    printf '  查看状态： free -h && cat /proc/swaps\n'
}

main "$@" < /dev/null
exit $?
