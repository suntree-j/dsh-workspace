#!/usr/bin/env bash
# ============================================================
# scripts/run-quality-checks.sh — 数据质量校验总入口（Sprint 11）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/run-quality-checks.sh                 # 全部 Doris 侧校验（快，不占额外内存）
#   bash scripts/run-quality-checks.sh --with-lake     # 追加湖仓 Iceberg 的层间校验（需要 ≈1 GB 空闲内存）
#   bash scripts/run-quality-checks.sh --list          # 只打印校验清单
#   bash scripts/run-quality-checks.sh --proof-fail    # **失败路径演示**：证明失败真的 exit 1
#   bash scripts/run-quality-checks.sh --json          # 机器可读结果（给调度/Airflow 用）
#
# 退出码：
#   0  全部 error 级校验通过
#   1  有 error 级校验失败（**大声失败**，不是"无论如何都返回 0"）
#   2  湖仓侧被闸门**拒绝**（内存不足，或检测到批处理正在跑）——
#      这是"拒绝执行"，**不是**校验失败，因此不算 1。与 batch-mode 的纪律一致：
#      宁可拒绝，也不要把内存打到 Doris 报 MEM_ALLOC_FAILED。
#
# ------------------------------------------------------------
# 为什么它必须是一个**独立、可被调度**的脚本（而不是塞进 run-batch-pipeline.sh）
# ------------------------------------------------------------
#   1) run-batch-pipeline.sh 是离线流水线的人工入口，改动它会影响
#      已验收的 DAG 与 batch-mode.sh（两条路径共用同一份阶段 case）；
#   2) 数据质量校验的触发时机不止一个：跑完批处理之后、定时巡检、
#      以及**人工怀疑数据有问题时**。做成独立入口，三种时机都能用同一个命令；
#   3) 与 health-check.sh 同一形态：能被 crontab / Airflow BashOperator /
#      CI 直接调用，且退出码有意义。
#
#   接进调度的两种方式（都**不改**别人的文件）：
#     a) Airflow：在 DAG 末尾加一个 BashOperator 跑本脚本
#        （DAG 文件属别人的范围，本 Sprint 只提供入口与说明）；
#     b) 手工/定时：跑完批处理后直接
#        bash scripts/run-quality-checks.sh --with-lake
#        见 docs/sprint/SPRINT_11.md 第 6 节。
#
#   --proof-fail 是给验收用的：它故意把一条校验的期望值改错，
#   证明"失败路径真的会 exit 1"。**只有它非 0 退出仍算预期**。
#
# ------------------------------------------------------------
# 内存纪律
# ------------------------------------------------------------
#   默认路径（Doris 侧）**不起 Spark**，因此可以在实时链路运行时随便跑。
#   --with-lake 会起一次性 spark-sql 容器（driver ≈1 GB），
#   所以它先过 scripts/lib/memory-guard.sh 的闸门：
#   可用内存不足时**拒绝执行**（退出码 2），而不是把 Doris 打到 MEM_ALLOC_FAILED。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"
# shellcheck source=lib/memory-guard.sh
source "${REPO_ROOT}/scripts/lib/memory-guard.sh"

ENGINE="${REPO_ROOT}/infrastructure/quality/run-check.sh"
WITH_LAKE=0
JSON_OUT=0
PROOF_FAIL=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --with-lake)  WITH_LAKE=1 ;;
        --json)       JSON_OUT=1 ;;
        --proof-fail) PROOF_FAIL=1 ;;
        --list)       bash "${ENGINE}" --list; exit $? ;;
        -h|--help)
            sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) log_error "未知参数：$1"; exit 2 ;;
    esac
    shift
done

# ------------------------------------------------------------
# 失败路径演示（--proof-fail）
#
# !! 为什么必须**故意**制造一次失败 !!
#   "失败必须大声失败"这句话无法用"看代码"来证明：
#   一个无论结果都 `exit 0` 的脚本，看代码也可能很像对的。
#   唯一可信的证据是：把期望值改成一个不可能成立的值，
#   看它是否真的打印 [FAIL] 并返回非 0。
#
#   这里同时演示两种"本该失败"的情形：
#     1) 实测值本身就是"差异数"的校验（amount_gmv_realtime，正常实测 0），
#        把期望值改成 999999999999 → 必然失败；
#     2) 一条本来恒真的校验（pk_unique_trade，正常实测 0），
#        把期望值改成 -1 → 必然失败。
#   两条都必须返回退出码 1；任一不是 1 → 演示本身失败（退出码 1）。
#
#   !! 引擎刻意**不提供**"忽略失败"或"放宽断言"的开关 !!
#   唯一的覆盖点就是期望值，而它只会让校验更容易失败、不会更容易通过。
# ------------------------------------------------------------
proof_fail() {
    load_env
    require_docker

    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 失败路径演示：证明校验真的会 exit 1${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    local rc_a=0 rc_b=0
    printf '\n%b\n' "${C_BOLD}▶ 演示 1：金额一致性（正常实测 0），期望值改成 999999999999${C_RESET}"
    bash "${ENGINE}" --id amount_gmv_realtime --expect-override 999999999999 || rc_a=$?

    printf '\n%b\n' "${C_BOLD}▶ 演示 2：主键唯一（正常实测 0，恒真），期望值改成 -1${C_RESET}"
    bash "${ENGINE}" --id pk_unique_trade --expect-override -1 || rc_b=$?

    printf '\n%b\n' "${C_BOLD}▶ 演示结论${C_RESET}"
    local ok=1
    if [ "${rc_a}" -eq 1 ]; then
        printf '  %b 演示 1 退出码 = 1（期望 1）—— 失败真的会 exit 1\n' "${C_GREEN}[ OK ]${C_RESET}"
    else
        printf '  %b 演示 1 退出码 = %s（期望 1）—— **失败没有被传播**\n' "${C_RED}[FAIL]${C_RESET}" "${rc_a}"
        ok=0
    fi
    if [ "${rc_b}" -eq 1 ]; then
        printf '  %b 演示 2 退出码 = 1（期望 1）—— 失败真的会 exit 1\n' "${C_GREEN}[ OK ]${C_RESET}"
    else
        printf '  %b 演示 2 退出码 = %s（期望 1）—— **失败没有被传播**\n' "${C_RED}[FAIL]${C_RESET}" "${rc_b}"
        ok=0
    fi

    printf '\n  说明：刚才两次都**没有**修改数据库，只是把期望值换掉再比一次。\n'
    printf '        紧接着跑一次正常校验，应当重新全绿：\n\n'
    bash "${ENGINE}" --id amount_gmv_realtime && printf '  %b 恢复后仍是 [OK]（说明失败来自断言，不是数据被改坏）\n' "${C_GREEN}[ OK ]${C_RESET}"

    [ "${ok}" -eq 1 ] || return 1

    # 再整体跑一遍，证明"默认路径"仍然是全绿且退出码 0
    printf '\n%b\n' "${C_BOLD}▶ 整体复跑（应当全绿、退出码 0）${C_RESET}"
    bash "${ENGINE}" --all || return 1
    return 0
}

if [ "${PROOF_FAIL}" -eq 1 ]; then
    proof_fail
    exit $?
fi

# ------------------------------------------------------------
# 把证据类 SQL 压成一行。
#
# !! 与 infrastructure/quality/run-check.sh 的 sql_oneline 同一理由 !!
#   直接 `tr -d '\n'` 会把开头那几行 `-- 注释` 与随后的 SELECT 粘成一行，
#   于是整条语句被注释吃掉：mysql 退出码 0、无输出 ——
#   看起来像"查询失败"，其实什么都没执行。
#   这里逐行丢弃整行注释，其余用空格连接。
# ------------------------------------------------------------
sql_oneline() {
    awk '
        { sub(/\r$/, "") }
        /^[[:space:]]*$/ { next }
        /^[[:space:]]*--/ { next }
        { gsub(/;[[:space:]]*$/, ""); printf "%s ", $0 }
    ' "$1"
}

main() {
    load_env
    require_docker
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 数据质量校验（Sprint 11）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '  校验清单： %s\n' "${REPO_ROOT}/infrastructure/quality/checks.conf"
    printf '  执行引擎： %s\n' "${ENGINE}"
    printf '  运行时刻： %s\n' "$(date '+%F %T %Z')"
    printf '  宿主机内存： '
    free -m | awk 'NR==2{printf "可用 %s MB / 共 %s MB\n", $7, $2}'

    local log="/tmp/quality-checks-$(date '+%Y%m%d-%H%M%S').log"
    printf '  完整日志： %s\n\n' "${log}"

    local rc=0
    bash "${ENGINE}" --all 2>&1 | tee "${log}" || rc=1

    # ---- 湖仓侧（Iceberg）层间校验：可选、受内存闸门约束 ----
    local lake_rc=0
    if [ "${WITH_LAKE}" -eq 1 ]; then
        printf '\n%b\n' "${C_BOLD}▶ 湖仓层间校验（Iceberg，engine=spark）${C_RESET}"

        # !! 闸门 1：与批处理互斥 !!
        #   每个 spark-sql 客户端 = 宿主机上约 1 GB 的驱动。
        #   如果此刻有人正在跑离线批处理（batch-mode / DAG 的 stage），
        #   再叠一个驱动就会把可用内存打到 1 GB 以下 ——
        #   那正是 Sprint 3 整机失联事故的成因（见 SPRINT_3.md 第 8 节）。
        #   与 batch-mode 的纪律一致：**拒绝，而不是硬跑**。
        if spark_jobs_running; then
            log_error "检测到 Spark 批处理作业正在运行，拒绝 --with-lake（避免 1 GB 驱动叠加）"
            printf '  这是**有意的拒绝**。可选做法：\n'
            printf '    a) 等批处理结束（pgrep -af '"'"'spark.deploy.SparkSubmi[t]'"'"' 确认为空）\n'
            printf '    b) 只跑 Doris 侧（不启 Spark）： bash scripts/run-quality-checks.sh\n'
            printf '    c) 需要湖仓结论时用错峰模式： bash scripts/batch-mode.sh --stage reconcile\n'
            lake_rc=2
        elif ! require_memory_for_batch; then
            # !! 闸门 2：可用内存不够 !!
            log_error "内存闸门拒绝 --with-lake（这是**有意的拒绝**，不是执行失败）"
            printf '  可选做法：\n'
            printf '    a) 用错峰模式先释放内存： bash scripts/batch-mode.sh --stage reconcile\n'
            printf '    b) 只跑 Doris 侧（不需要额外内存）： bash scripts/run-quality-checks.sh\n'
            lake_rc=2
        else
            local id
            # 逐条取 engine=spark 的项来跑（清单解析复用 python，避免两处实现）
            while IFS= read -r id; do
                [ -n "${id}" ] || continue
                if bash "${ENGINE}" --id "${id}" 2>&1 | tee -a "${log}"; then
                    :
                else
                    lake_rc=1
                fi
            done < <(python3 - "${REPO_ROOT}/infrastructure/quality/checks.conf" <<'PY'
import re, sys
cur = None
for raw in open(sys.argv[1], encoding='utf-8'):
    line = raw.rstrip('\n')
    if line.lstrip().startswith('#') or not line.strip():
        continue
    m = re.match(r'^\[check\.([A-Za-z0-9_]+)\]\s*$', line)
    if m:
        cur = {'id': m.group(1)}; continue
    if cur is None:
        continue
    m = re.match(r'^([a-z_]+)\s+(.*)$', line)
    if m:
        cur[m.group(1)] = m.group(2).strip()
        if m.group(1) == 'engine' and m.group(2).strip() == 'spark':
            print(cur.get('id', ''))
PY
            )
        fi
    fi

    # ---- 实测快照：把"回到目标状态验证"变成每次运行都会留下的证据 ----
    printf '\n%b\n' "${C_BOLD}▶ 实测快照${C_RESET}"
    local snap
    snap="$(compose exec -T doris-be mysql -h 172.28.0.10 -P 9030 -uroot -N \
            -e "$(sql_oneline "${REPO_ROOT}/infrastructure/quality/checks/_evidence_counts.sql")" 2>/dev/null \
            | grep -vE '^[[:space:]]*$' | tail -n 1)"
    if [ -n "${snap}" ]; then
        printf '  orders/payments/refunds/behaviors/ads_trade_1m/ads_traffic_1m/gmv_realtime/gmv_batch：\n    %s\n' "${snap}"
    else
        printf '  %b 取不到快照（Doris 不可用？）\n' "${C_YELLOW}[WARN]${C_RESET}"
    fi

    local fresh
    fresh="$(compose exec -T doris-be mysql -h 172.28.0.10 -P 9030 -uroot -N \
            -e "$(sql_oneline "${REPO_ROOT}/infrastructure/quality/checks/_evidence_freshness.sql")" 2>/dev/null \
            | grep -vE '^[[:space:]]*$' | tail -n 1)"
    [ -n "${fresh}" ] && printf '  新鲜度证据（now / 最新窗口 / 滞后小时）：\n    %s\n' "${fresh}"

    printf '\n%b\n' "${C_BOLD}=====================================${C_RESET}"
    if [ "${rc}" -eq 0 ] && [ "${lake_rc}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} 数据质量校验全部通过${C_RESET}"
    elif [ "${lake_rc}" -eq 2 ]; then
        printf '%b\n' "${C_YELLOW}${C_BOLD} Doris 侧通过；湖仓侧被闸门拒绝（未执行，不算失败）${C_RESET}"
        printf '  拒绝原因见上方：可用内存不足，或检测到 Spark 批处理正在运行。\n'
        printf '  这不是校验失败 —— 数据质量结论以 Doris 侧 24 条为准。\n'
    else
        printf '%b\n' "${C_RED}${C_BOLD} 数据质量校验存在失败项 —— 退出码 1${C_RESET}"
        printf '  日志： %s\n' "${log}"
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    if [ "${JSON_OUT}" -eq 1 ]; then
        printf '{"script":"run-quality-checks","doris_rc":%s,"lake_rc":%s,"log":"%s","ts":"%s"}\n' \
            "${rc}" "${lake_rc}" "${log}" "$(date -Iseconds)"
    fi

    if [ "${rc}" -ne 0 ]; then return 1; fi
    [ "${lake_rc}" -eq 1 ] && return 1
    return 0
}

main "$@" < /dev/null
exit $?
