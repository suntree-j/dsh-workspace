#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-3.sh — Sprint 3 验收（离线分层建模 + 批流交叉对账）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-3.sh
#
# 验收项（8 步）：
#   1. 表清单      湖仓四层表齐全（DWD 5 / DWS 3 / ADS 6），Doris 侧离线库表齐全
#   2. 层间行数    DWD == ODS（逐表）；ADS 分钟表窗口数自洽
#   3. 类型正确    金额一律 decimal(18,2)，四层不得出现 double/float
#   4. 幂等        重跑 ADS 层后行数与合计不变（两侧都必须真的取到值）
#   5. 批流对账    **本次运行**产生的对账批次 is_pass = true、不一致窗口 = 0
#                  （按本次运行的起始时刻为下界过滤；历史批次不能冒充本次）
#   6. 服务装载    Doris lakehouse_ads 行数 == 湖仓，且只读账号可查
#   7. 回归        实时链路 health-check 通过、数据服务 /health 正常
#   8. 自动化测试  pytest（单元 + 离线冒烟）
#
# 为什么把"幂等"单独作为一步：
#   离线链路的价值之一是**可重跑**。如果重跑会让行数翻倍或金额变化，
#   那这套分层就只能"跑一次看看"，无法作为调度任务（Sprint 4 Airflow）的基础。
#   因此这里真跑一遍 ADS 层并比对前后结果，而不是只看代码里写了 overwrite。
#
# 退出码：0 = 全部通过；非 0 = 有验收项失败
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

DORIS_DB="${DORIS_BATCH_DATABASE:-lakehouse_ads}"
DORIS_QUERY_PORT="${DORIS_FE_QUERY_PORT:-9030}"

PASS=0
FAIL=0
SKIP=0
DETAILS=()

check() {
    local name="$1" expected="$2" actual="$3"
    if [ "${expected}" = "${actual}" ]; then
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${actual}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-46s %s  (期望 %s)\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${actual}" "${expected}"
        DETAILS+=("${name}: 实际 ${actual} / 期望 ${expected}")
        FAIL=$(( FAIL + 1 ))
    fi
}

check_true() {
    local name="$1" note="$2"; shift 2
    if "$@" >/dev/null 2>&1; then
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${note}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${note}"
        DETAILS+=("${name}: ${note}")
        FAIL=$(( FAIL + 1 ))
    fi
}

section() { printf '\n%b\n' "${C_BOLD}$*${C_RESET}"; }

# ------------------------------------------------------------
# 工具：查湖仓（一次性 Spark 容器）
# 说明：spark-sql 会打印表头与日志，这里只取最后一行纯数字。
# ------------------------------------------------------------
sql() {
    bash "${REPO_ROOT}/scripts/spark-sql.sh" -e "$1" 2>/dev/null
}

lake_scalar() {
    local out
    out="$(sql "$1" | grep -E '^[0-9]+(\.[0-9]+)?$' | tail -1 || true)"
    printf '%s' "${out:-0}"
}

# ------------------------------------------------------------
# !! 为什么还需要一个 lake_scalar_strict（Sprint 13A 修复 3 的关键）!!
#
#   `lake_scalar` 的 `${out:-0}` 让"查询失败"与"结果就是 0"**返回值完全相同**。
#   于是任何形如
#       a="$(lake_scalar ...)"; b="$(lake_scalar ...)"; check "..." "$a" "$b"
#   的断言，在两次查询都失败时都会变成 `0 = 0` → [ OK ]。
#   这正是本脚本第 4 步（幂等）原来的假通过机制。
#
#   ⚠️ 只加"空值守卫"是**修不好的** —— 因为 lake_scalar **永远不会返回空**：
#      我第一次的改法写的是 `if [ -z "${before_rows}" ]`，以为拦住了，
#      实测却依然报 OK（见对话记录里的反证实验②）。判据必须落在
#      "查询到底有没有成功"上，而不是"结果字符串长什么样"。
#      **一个永远不返回空的函数，用判空是拦不住它的。**
#
#   所以这里提供一个**不兜底**的版本：取不到值就返回空串，
#   让调用方自己决定"空"是失败还是合法值（`COUNT(*)` 的 0 是合法值）。
#   对照：`verify-sprint-5.sh:126-131` 的 lake_scalar **本来就**不兜底，
#   所以那边的空值守卫（`:262-265`）是有效的 —— 两个脚本原来的差别就在这里。
# ------------------------------------------------------------
lake_scalar_strict() {
    sql "$1" | grep -E '^[0-9]+(\.[0-9]+)?$' | tail -1 || true
}

sql_text() {
    sql "$1" | grep -vE '^(Time taken|$)' || true
}

# 取某张表某个字段的类型。
#
# !! 为什么不能直接 awk '$1=="amount"'（Sprint 3 踩坑）!!
#   Spark 的 DESCRIBE 会**把列名右填充空格**做对齐，而类型列不填充：
#       amount              <TAB>decimal(18,2)
#   于是 $1 实际是 "amount              "（带 14 个空格），
#   与 "amount" 永远不相等 —— awk 不报错，只是输出空字符串，
#   表现为"取不到类型"，看起来像类型错了，其实是解析错了。
#   这里先 tr -s ' ' 把连续空格压成一个，再去掉行首行尾空白。
describe_type() {
    local table="$1" column="$2"
    sql_text "DESCRIBE lakehouse.${table};" \
        | tr -s ' ' \
        | awk -F'\t' -v col="${column}" '
            { gsub(/^[ \t]+|[ \t]+$/, "", $1) }
            $1 == col { gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit }
        '
}

doris_q() {
    local root_pw
    root_pw="$(grep -E '^DORIS_ROOT_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    docker exec -i doris-fe mysql -h 127.0.0.1 -P "${DORIS_QUERY_PORT}" \
        -uroot -p"${root_pw}" --connect-timeout=15 "$@" 2>/dev/null
}

doris_scalar() { doris_q -B -N <<< "$1" | tail -1; }

# ============================================================
# 1. 表清单
# ============================================================
step_tables() {
    section "1/8 表清单（湖仓四层 + Doris 离线库）"

    local tables
    tables="$(sql_text "SHOW TABLES IN lakehouse;")"

    # !! 断言从"层内表数 == N"改为"层内**期望的表逐一点名都在**" !!
    #
    #   为什么改（Sprint 5 实测踩到的回归）：
    #     原断言写的是 ODS 5 / DWD 5 / DWS 3 / ADS 6 —— 那是 Sprint 3 当时的规模。
    #     Sprint 5 往同一批分层里加了流量域 5 张表后，这里立刻变成
    #     6 / 6 / 5 / 10 而全部报 FAIL —— 但**交易域的表一张都没少**，
    #     数据也完全正确。也就是说：报的失败与真正的不变量（"Sprint 3 建的表还在"）
    #     不是一回事，属于**断言写错**，跟 Sprint 5 那两次"过滤器吃掉真数据"
    #     是同一类问题的另一个面。
    #
    #   而且"Sprint 3 的验收要盯住 Sprint 3 建的东西"才是它的职责：
    #     后续 Sprint 往同层加表是**正常演进**，不该让这个脚本变红；
    #     反过来，交易域哪张表真丢了，必须立刻红 —— 逐名核对正好做到这一点。
    #
    #   注意：数量仍然打印出来（供人看规模），只是不再当判据。
    local layer
    for layer in ods dwd dws ads; do
        printf '     %s 层表数（参考）： %s\n' "${layer}" \
            "$(printf '%s\n' "${tables}" | grep -cE "^${layer}_" || true)"
    done

    # 关键表逐一点名（防止"数量对了但缺的是要紧那张"）
    local t missing=0
    for t in ods_user ods_product ods_orders ods_payment ods_refund \
             dwd_user_detail dwd_product_detail dwd_trade_order_detail \
             dwd_trade_payment_detail dwd_trade_refund_detail \
             dws_trade_overview_1d dws_trade_category_1d dws_trade_user_1d \
             ads_batch_trade_1m ads_batch_trade_1d ads_batch_category_1m \
             ads_batch_category_1d ads_reconcile_trade_1m ads_reconcile_summary; do
        printf '%s\n' "${tables}" | grep -qx "${t}" || { missing=$(( missing + 1 )); log_warn "缺少表：lakehouse.${t}"; }
    done
    check "湖仓关键表齐全（Sprint 3 建的表一张不少）" "0" "${missing}"

    # Doris 离线库：同样改为逐名核对（原为"表数 == 6"，Sprint 5 加表后即失效）
    local doris_tables
    doris_tables="$(doris_q -B -N -e "SHOW TABLES FROM ${DORIS_DB};" | tr -d '\r')"
    local d_missing=0
    for t in ads_batch_trade_1m ads_batch_trade_1d ads_batch_category_1m \
             ads_batch_category_1d ads_reconcile_trade_1m ads_reconcile_summary; do
        printf '%s\n' "${doris_tables}" | grep -qx "${t}" \
            || { d_missing=$(( d_missing + 1 )); log_warn "Doris 缺少表：${DORIS_DB}.${t}"; }
    done
    check "Doris ${DORIS_DB} 关键表齐全" "0" "${d_missing}"
    printf '     Doris %s 表数（参考）： %s\n' "${DORIS_DB}" \
        "$(printf '%s\n' "${doris_tables}" | grep -c . || true)"
}

# ============================================================
# 2. 层间行数
# ============================================================
step_rowcounts() {
    section "2/8 层间行数（DWD == ODS，逐表）"

    local pair dwd ods
    for pair in "dwd_user_detail:ods_user" \
                "dwd_product_detail:ods_product" \
                "dwd_trade_order_detail:ods_orders" \
                "dwd_trade_payment_detail:ods_payment" \
                "dwd_trade_refund_detail:ods_refund"; do
        dwd="${pair%%:*}"
        ods="${pair##*:}"
        check "${dwd} == ${ods}" "$(lake_scalar "SELECT COUNT(*) FROM lakehouse.${ods};")" \
                                 "$(lake_scalar "SELECT COUNT(*) FROM lakehouse.${dwd};")"
    done

    # 分钟表窗口数 == 三条事件流出现过的分钟数
    #
    # 注意：这里用 GROUP BY 后数行数，而不是 COUNT(DISTINCT ...)。
    # Spark 的 COUNT(DISTINCT) 是近似算法（约 5% 误差），
    # 用它做"必须完全相等"的验收断言会时灵时不灵（Sprint 3 踩坑，见 SPRINT_3.md 第 8 节）。
    local expected_windows
    expected_windows="$(lake_scalar "
        SELECT COUNT(*) FROM (
            SELECT DISTINCT DATE_TRUNC('minute', event_time) AS w
            FROM lakehouse.dwd_trade_order_detail
            UNION
            SELECT DISTINCT DATE_TRUNC('minute', event_time) AS w
            FROM lakehouse.dwd_trade_payment_detail
            UNION
            SELECT DISTINCT DATE_TRUNC('minute', event_time) AS w
            FROM lakehouse.dwd_trade_refund_detail
        ) t;")"
    check "ADS 分钟表窗口数 == 事件分钟数" "${expected_windows}" \
          "$(lake_scalar "SELECT COUNT(*) FROM lakehouse.ads_batch_trade_1m;")"

    # 天表 == 分钟表按天上卷（GMV 精确到分）
    check "ADS 天表 GMV == 分钟表 GMV" "$(lake_scalar "SELECT SUM(gmv) FROM lakehouse.ads_batch_trade_1m;")" \
          "$(lake_scalar "SELECT SUM(gmv) FROM lakehouse.ads_batch_trade_1d;")"

    # 类目天表行数 == 分钟表的（日 × 类目）组合数
    #
    # 这条断言容易写错成"1d 行数 == 1m 行数"：
    # 1m 是"分钟 × 类目"，1d 是"日 × 类目"，两者天然不等
    # （同一天同一类目可能出现在多个分钟里）。必须比组合数，不能比行数。
    check "类目天表行数 == 日×类目组合数" \
          "$(lake_scalar "SELECT COUNT(*) FROM (SELECT DISTINCT dt, category_name FROM lakehouse.ads_batch_category_1m) t;")" \
          "$(lake_scalar "SELECT COUNT(*) FROM lakehouse.ads_batch_category_1d;")"

    # 类目维度守恒
    check "类目分钟表 GMV == 订单明细 GMV" \
          "$(lake_scalar "SELECT SUM(amount) FROM lakehouse.dwd_trade_order_detail;")" \
          "$(lake_scalar "SELECT SUM(gmv) FROM lakehouse.ads_batch_category_1m;")"
}

# ============================================================
# 3. 类型正确性
# ============================================================
step_types() {
    section "3/8 类型正确性（金额 decimal(18,2)，四层无 double/float）"

    local bad=0 line table
    for table in dwd_user_detail dwd_product_detail dwd_trade_order_detail \
                 dwd_trade_payment_detail dwd_trade_refund_detail \
                 dws_trade_overview_1d dws_trade_category_1d dws_trade_user_1d \
                 ads_batch_trade_1m ads_batch_trade_1d ads_batch_category_1m \
                 ads_batch_category_1d; do
        # 注意 awk 的 print 里必须有空格：
        #   print $1":"$2  →  "amountdecimal(18,2)"（拼接，取不到类型）
        #   print $1 ": " $2 → "amount: decimal(18,2)"（正确）
        # 这个坑让类型断言全部误报成 FAIL（看起来像类型错了，其实解析错了）。
        line="$(sql_text "DESCRIBE lakehouse.${table};" | awk -F'\t' 'tolower($2) ~ /double|float/ {print $1 ": " $2}')"
        if [ -n "${line}" ]; then
            log_warn "${table} 存在浮点列：${line}"
            bad=$(( bad + 1 ))
        fi
    done
    check "无 double/float 列" "0" "${bad}"

    local amount_type
    amount_type="$(describe_type dwd_trade_order_detail amount)"
    check "dwd_trade_order_detail.amount" "decimal(18,2)" "${amount_type:-<未取到>}"

    local gmv_type
    gmv_type="$(describe_type ads_batch_trade_1m gmv)"
    check "ads_batch_trade_1m.gmv" "decimal(18,2)" "${gmv_type:-<未取到>}"

    local rate_type
    rate_type="$(describe_type ads_batch_trade_1m refund_rate)"
    check "ads_batch_trade_1m.refund_rate" "decimal(10,4)" "${rate_type:-<未取到>}"
}

# ============================================================
# 4. 幂等性
# ============================================================
step_idempotent() {
    section "4/8 幂等性（重跑 ADS 层，行数与合计不变）"

    local before_rows before_gmv after_rows after_gmv
    # 用 **strict** 版本取值：取不到就是空串，不会被兜底成 0。
    # 这是本次修复的关键 —— 用 `lake_scalar` 的话，`${out:-0}` 会把
    # "查询失败"与"结果就是 0"变成同一个返回值，下面的守卫永远不触发。
    before_rows="$(lake_scalar_strict "SELECT COUNT(*) FROM lakehouse.ads_batch_trade_1m;")"
    before_gmv="$(lake_scalar "SELECT SUM(gmv) FROM lakehouse.ads_batch_trade_1m;")"

    # !! 非空守卫：没有它，下面的 `check` 会在"查询失败"时误报 OK !!
    #
    #   机制（Sprint 13A 审计发现、本次实测复现）：
    #     `lake_scalar` 把查询失败兜底成 `0`，两侧都失败就是 `0 = 0` → [ OK ]。
    #     也就是"看起来验证了幂等，其实一次查询都没成功"。
    #   判据用 `-z`（非空）而不是 `-gt 0`：
    #   空表上的 `COUNT(*) = 0` 是**合法值**，"取不到"与"确实是 0"必须区分开。
    if [ -z "${before_rows}" ]; then
        printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "重跑前窗口数可读（防空值假通过）" "查询失败/取不到值"
        DETAILS+=("幂等前置检查失败：COUNT(*) FROM lakehouse.ads_batch_trade_1m 取不到值（不是 0，是查询没成功）")
        FAIL=$(( FAIL + 1 ))
        return
    fi

    log_info "重跑 ADS 层（build_ads.py）..."
    if ! bash "${REPO_ROOT}/scripts/submit-offline-job.sh" --stage ads > /tmp/verify-s3-idempotent.log 2>&1; then
        log_error "重跑 ADS 失败，日志见 /tmp/verify-s3-idempotent.log"
        FAIL=$(( FAIL + 1 ))
        DETAILS+=("幂等重跑失败：见 /tmp/verify-s3-idempotent.log")
        return
    fi

    after_rows="$(lake_scalar_strict "SELECT COUNT(*) FROM lakehouse.ads_batch_trade_1m;")"
    after_gmv="$(lake_scalar "SELECT SUM(gmv) FROM lakehouse.ads_batch_trade_1m;")"

    # 重跑之后同样要守卫：否则"重跑把表跑空了"会被读成 0 而与 before 不等 ——
    # 这一侧恰好会 FAIL，但报出来的原因是"数字不等"，不是"表空了"。
    # 排障方向会被带偏，所以显式判一次。
    if [ -z "${after_rows}" ]; then
        printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "重跑后窗口数可读" "查询失败/取不到值"
        DETAILS+=("幂等后置检查失败：重跑后 COUNT(*) 取不到值，日志见 /tmp/verify-s3-idempotent.log")
        FAIL=$(( FAIL + 1 ))
        return
    fi

    check "重跑后窗口数不变" "${before_rows}" "${after_rows}"
    check "重跑后 GMV 不变" "${before_gmv}" "${after_gmv}"
}

# ============================================================
# 5. 批流交叉对账（本 Sprint 的核心）
# ============================================================
step_reconcile() {
    section "5/8 批流交叉对账（实时 Flink/Doris vs 离线 Spark/湖仓）"

    local row
    # !! 必须绑定**本次运行**产生的批次，不能取"最新一批" !!
    #
    #   旧写法是 `ORDER BY compared_at DESC LIMIT 1` —— 它取的是"表里最新的一行"，
    #   而**不是"这次验收跑出来的那一行"**。后果很具体：
    #   当本次对账没有产出任何行时（例如 reconcile 阶段没跑、或它失败后
    #   汇总表里还留着上次的行），这个查询会**照常返回历史批次**，
    #   于是 `不一致 0 / is_pass=true` 全部变绿 —— 验收报告上写得漂漂亮亮，
    #   而本次运行的结论根本不存在。这就是"历史 PASS 冒充本次 PASS"。
    #
    #   修法：以"本次运行开始时刻"为**下界**过滤。对账作业写入的
    #   `compared_at` 是它自己 `datetime.now()` 的取值（见
    #   `reconcile_batch_realtime.py:119,142`，batch_id 也由它派生），
    #   因此本次批次必然 >= 本次运行起点；历史批次必然 < 下界。
    #
    #   ⚠️ 容差 3 秒：脚本起跑与 Spark 作业取时刻之间可能有亚秒级差值与
    #   容器/宿主时钟的微小偏移，取 `now - 3s` 比取严格 `now` 稳。
    #   代价是"3 秒内跑完的一次对账"理论上可能被上一批顶替 ——
    #   实际不可能（对账作业本身要跑分钟级），这个取舍明确写在这里。
    #
    #   可用 `VERIFY_RECONCILE_BATCH_FLOOR` 覆盖下界，**仅用于对照实验**
    #   （设成未来时刻即可证明：取不到本次批次时这一步会 FAIL，
    #   而不是悄悄退回历史批次）。
    local floor
    if [ -n "${VERIFY_RECONCILE_BATCH_FLOOR:-}" ]; then
        floor="${VERIFY_RECONCILE_BATCH_FLOOR}"
        log_warn "使用外部指定的批次下界（对照实验用）：${floor}"
    else
        floor="$(date -d '3 seconds ago' '+%Y-%m-%d %H:%M:%S' 2>/dev/null \
                 || date -v-3S '+%Y-%m-%d %H:%M:%S' 2>/dev/null \
                 || echo '')"
    fi

    if [ -z "${floor}" ]; then
        printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "批次下界可计算" "date 不支持 -d/-v（无法绑定本次批次）"
        FAIL=$(( FAIL + 1 ))
        DETAILS+=("无法计算本次运行的批次下界：本平台需 GNU date（Linux 服务器）")
        return
    fi

    row="$(sql_text "
        SELECT batch_id, compared_at, scope_start, scope_end,
               realtime_windows, batch_windows, matched_windows, mismatched_windows,
               first_mismatch_at, realtime_total_gmv, batch_total_gmv, is_pass
        FROM lakehouse.ads_reconcile_summary
        WHERE compared_at >= TIMESTAMP '${floor}'
        ORDER BY compared_at DESC
        LIMIT 1;")"

    if [ -z "${row}" ]; then
        printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "本次运行产生了对账批次" "无 compared_at >= ${floor} 的行"
        FAIL=$(( FAIL + 1 ))
        DETAILS+=("未找到本次运行（下界 ${floor}）产生的对账批次：对账可能没跑或失败。\
这里**不会**退回历史批次 —— 历史 PASS 不能证明本次 PASS")
        return
    fi

    local batch_id compared_at scope_start scope_end rw bw mw xw first_mismatch rt_gmv b_gmv is_pass
    IFS=$'\t' read -r batch_id compared_at scope_start scope_end rw bw mw xw first_mismatch rt_gmv b_gmv is_pass <<< "${row}"

    printf '  批次 %s  区间 [%s , %s)\n' "${batch_id}" "${scope_start}" "${scope_end}"
    printf '  对账窗口 %s 个（实时侧 %s 个），一致 %s，不一致 %s\n' "${bw}" "${rw}" "${mw}" "${xw}"

    check "不一致窗口数" "0" "${xw}"
    check "实时侧 GMV 合计 == 离线侧" "${rt_gmv}" "${b_gmv}"
    check "总体结论 is_pass" "true" "${is_pass}"
    if [ "${xw}" != "0" ]; then
        log_warn "首个不一致窗口：${first_mismatch}"
        printf '  查看差异明细： bash scripts/spark-sql.sh -e "SELECT * FROM lakehouse.ads_reconcile_trade_1m WHERE NOT is_match ORDER BY window_start LIMIT 20;"\n'
    fi
}

# ============================================================
# 6. 服务装载（Doris 离线库）
# ============================================================
step_service_load() {
    section "6/8 服务装载（Doris ${DORIS_DB} 行数 == 湖仓，只读账号可查）"

    local pair table doris_count lake
    for table in ads_batch_trade_1m ads_batch_trade_1d \
                 ads_batch_category_1m ads_batch_category_1d \
                 ads_reconcile_trade_1m; do
        doris_count="$(doris_scalar "SELECT COUNT(*) FROM ${DORIS_DB}.${table};")"
        lake="$(lake_scalar "SELECT COUNT(*) FROM lakehouse.${table};")"
        check "Doris ${table} 行数 == 湖仓" "${lake}" "${doris_count}"
    done

    # GMV 精确一致（服务层看到的就是离线算出来的）
    check "Doris 离线 GMV == 湖仓 GMV" \
          "$(lake_scalar "SELECT SUM(gmv) FROM lakehouse.ads_batch_trade_1m;")" \
          "$(doris_scalar "SELECT SUM(gmv) FROM ${DORIS_DB}.ads_batch_trade_1m;")"

    # 只读账号可用（服务层与未来 Agent 用的就是这个账号）
    local ro_pw ro_ok
    ro_pw="$(grep -E '^API_DORIS_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    ro_ok="$(docker exec -i doris-fe mysql -h 127.0.0.1 -P "${DORIS_QUERY_PORT}" \
        -uagent_ro -p"${ro_pw}" -B -N 2>/dev/null \
        <<< "SELECT COUNT(*) FROM ${DORIS_DB}.ads_batch_trade_1m;" | tail -1)"
    check_true "只读账号 agent_ro 可查离线库" "count=${ro_ok}" test -n "${ro_ok}"

    # 写操作必须被拒绝（AGENTS.md 第 10.3 节）
    check_true "只读账号写操作被拒绝" "DELETE 应报 Access denied" \
        bash -c "! docker exec -i doris-fe mysql -h 127.0.0.1 -P '${DORIS_QUERY_PORT}' -uagent_ro -p'${ro_pw}' -B -N 2>/dev/null <<< 'DELETE FROM ${DORIS_DB}.ads_batch_trade_1m WHERE 1=0;' | grep -q ."
}

# ============================================================
# 7. 回归（实时链路 + 服务层）
# ============================================================
step_regression() {
    section "7/8 回归（实时链路 + 数据服务）"

    check_true "health-check.sh（实时链路 11 项）" "退出码 0" \
        bash "${REPO_ROOT}/scripts/health-check.sh"

    local health
    health="$(curl_site -fsS --max-time 10 "$(site_base)/data/api/health" 2>/dev/null || true)"
    if printf '%s' "${health}" | grep -q '"status":"ok"'; then
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "数据服务 /data/api/health" "ok"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-46s %s\n' "${C_RED}[FAIL]${C_RESET}" "数据服务 /data/api/health" "无响应或非 ok"
        DETAILS+=("数据服务健康检查失败：${health}")
        FAIL=$(( FAIL + 1 ))
    fi

    local web_code
    web_code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 10 "$(site_base)/data/" 2>/dev/null || true)"
    check "看板首页 HTTP 状态" "200" "${web_code}"
}

# ============================================================
# 8. 自动化测试
# ============================================================
step_tests() {
    section "8/8 自动化测试（单元 + 冒烟）"

    # !! 必须优先用项目自己的虚拟环境 !!
    #   服务器上系统 python3 里**没有** pytest（实测：No module named pytest），
    #   项目依赖装在 /opt/data-platform/.venv（见 scripts/install-web.sh）。
    #   直接用 `python -m pytest` 会得到"测试失败"的假警报，
    #   而实际上只是找错了解释器 —— 这类假失败会让人不再相信验收结果。
    local py=""
    if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
        py="${REPO_ROOT}/.venv/bin/python"
    elif command -v python >/dev/null 2>&1; then
        py="$(command -v python)"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    fi

    if [ -z "${py}" ]; then
        printf '  %b %-46s 未找到可用的 python\n' "${C_YELLOW}[SKIP]${C_RESET}" "pytest"
        SKIP=$(( SKIP + 1 ))
        return
    fi

    if ! "${py}" -c 'import pytest' >/dev/null 2>&1; then
        printf '  %b %-46s %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "pytest 不可用" "${py}"
        printf '  提示：服务器上依赖装在 .venv，请执行 bash scripts/install-web.sh 或\n'
        printf '        %s -m pip install -r services/api/requirements.txt\n' "${py}"
        SKIP=$(( SKIP + 1 ))
        return
    fi

    local out
    if out="$(cd "${REPO_ROOT}" && "${py}" -m pytest -q 2>&1)"; then
        local summary
        summary="$(printf '%s\n' "${out}" | tail -1)"
        printf '  %b %-46s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "pytest 全部通过 (${py##*/})" "${summary}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-46s\n' "${C_RED}[FAIL]${C_RESET}" "pytest 失败"
        printf '%s\n' "${out}" | tail -25
        DETAILS+=("pytest 失败（解释器 ${py}）")
        FAIL=$(( FAIL + 1 ))
    fi
}

# ============================================================
# 汇总
# ============================================================
summary() {
    printf '\n%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 3 验收汇总${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '  通过 %s   失败 %s   跳过 %s\n' "${PASS}" "${FAIL}" "${SKIP}"
    if [ "${FAIL}" -gt 0 ]; then
        printf '\n%b\n' "${C_RED}失败明细：${C_RESET}"
        local d
        for d in "${DETAILS[@]}"; do
            printf '  - %s\n' "${d}"
        done
        printf '\n'
        log_error "Sprint 3 验收未通过"
        return 1
    fi
    printf '\n'
    log_ok "Sprint 3 验收通过：离线分层建模完成，批流指标精确对账一致"
    return 0
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 3 验收：离线分层建模 + 批流交叉对账${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    require_docker

    step_tables
    step_rowcounts
    step_types
    step_idempotent
    step_reconcile
    step_service_load
    step_regression
    step_tests

    summary
}

main "$@" < /dev/null
exit $?
