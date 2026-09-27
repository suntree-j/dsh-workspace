#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-5.sh — Sprint 5 验收（Iceberg Lakehouse + 流量域分层与对账）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-5.sh
#
# 验收 8 步：
#   1  阶段接入自检（STAGES / --list / case / 兜底分支 / 白名单，五处一致）
#   2  流量域 DWD（行数 / 主键唯一 / 枚举 / 漏斗基线 / 字段与实时侧同形）
#   3  流量域 DWS + ADS（层间可加量闭合 + uv 去重口径正确 + 与实时侧同形）
#   4  Iceberg 侧核对（23 张表、Provider==iceberg、行数与 Parquet 侧逐表一致）
#   5  **流量域批流对账**（逐窗口全量比对、差异 0、单边窗口 0）
#   6  对账证据与口径（区间可核 / 去重指标不可加的实证 / 比率列独立核对）
#   7  内存与执行方式（仍走错峰模式、闸门未被放宽）
#   8  回归（**轻量**：健康检查 + 交易域对账仍在 + 两个入口可达 + 关键服务 active）
#
# !! 第 8 步为什么从"整链回归"降级为"轻量回归"（本 Sprint 的降噪改动）!!
#
#   原来的第 8 步会**再跑一遍** verify-sprint-3.sh 与 verify-sprint-4.sh，
#   而这两个脚本各自都要驱动一次离线批处理（Spark 作业链）——
#   于是"验收 Sprint 5"这件事被放大到 25 分钟以上，
#   实际使用中只能在跑到一半时被掐断（掐断之后连已通过的部分也拿不到汇总）。
#
#   这是一次**明确降低覆盖面**的改动，因此必须留下移交记录，不许悄悄去掉：
#     - 交易域/流量域的**整链验收**：`bash scripts/verify-sprint-3.sh`
#                                     `bash scripts/verify-sprint-4.sh`
#     - **统一回归入口**（Sprint 12 起）：`bash scripts/verify-sprint-12.sh`
#   本步骤只回答一个问题："我这次改的东西，有没有把别人已经验收过的东西弄坏？"
#   它用**外部可观测面**回答（健康检查数、对账表结论、HTTP 入口、服务状态），
#   不重新做一遍别人的离线计算 —— 那属于 Sprint 12 的统一回归。
#
#   为什么留下的偏偏是这几条：它们覆盖了**被本 Sprint 改动的东西所在的路径**
#   （Doris 装载结果 → 只读服务 → 看板/Agent 入口 → systemd 服务）。
#   轻量不等于随便挑：留下的是最短的那条"改动 → 暴露面"链路。
#
# !! 为什么第 1 步是"阶段接入自检"而不是"看表在不在" !!
#   Sprint 4 实测过一个缺陷：阶段加进了 STAGES 与 --list，
#   却漏加 run_stage 的 case 分支 —— case 穿透、rc 保持 0，
#   于是**一个什么都没做的阶段报告成功了**，Airflow 也把它标成 success。
#   这类错误的产物是"看起来做完了、实际 0 行"，
#   而它的根因在脚本结构的**一致性**上，不在数据里。
#   所以这里把"五处清单是否一致"做成可执行的断言，
#   而不是靠人读脚本确认。
#
# !! 为什么第 6 步要专门验"去重指标不可加" !!
#   UV 是本次对账判据里唯一的去重指标。这条口径错了，
#   对账照样能"通过"（如果两侧都错成一样），但结论是假的。
#   本项目实测：SUM(窗口 uv) = 19999，全局去重 = 1199，差 16 倍。
#   所以这里断言的不是"某个数等于某个数"，而是
#   "窗口 uv 之和 > 全局去重用户数" —— 后者成立才说明口径是去重口径。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

PASS=0
FAIL=0
SKIP=0
STEP_NAMES=()
STEP_RESULTS=()
DETAILS=()
OVERALL=0

step_start() { STEP_NAMES+=("$1"); printf '\n%b\n' "${C_BOLD}$*${C_RESET}"; }
step_record() {
    STEP_RESULTS+=("$2")
    if [ "$2" != "PASS" ] && [ "$2" != "SKIP" ]; then OVERALL=1; fi
}

check() {
    local name="$1" expected="$2" actual="$3"
    if [ "${expected}" = "${actual}" ]; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${actual}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s 期望=[%s] 实际=[%s]\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${expected}" "${actual}"
        DETAILS+=("${name}: 期望 ${expected}，实际 ${actual}")
        FAIL=$(( FAIL + 1 ))
    fi
}

check_true() {
    local name="$1" note="$2"
    shift 2
    if "$@"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${note}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${note}"
        DETAILS+=("${name}: 条件不成立（${note}）")
        FAIL=$(( FAIL + 1 ))
    fi
}

section() { printf '\n%b\n' "${C_BLUE}$*${C_RESET}"; }

# 从 .env 取值（不回显口令）
envv() { grep -E "^$1=" "${REPO_ROOT}/.env" 2>/dev/null | head -1 | cut -d= -f2-; }

# 查湖仓标量的通用入口（用一次性容器，不依赖 Spark 集群状态）
#
# !! 不要用 `tail -1` 抓结果 !!
#   spark-sql 的 stdout 末尾会跟 `Time taken: x seconds`，而结果行在它**之前**；
#   但结果行本身也可能被 WARN 包围。这里按"只保留看起来像标量的行"过滤，
#   最后再取最后一行 —— 比单纯 tail 稳。
#
# !! 过滤器的教训（Sprint 5 实测踩坑，务必看）!!
#   最初这里的过滤器带了一条 `^[a-z_]+$`，本意是丢掉表头。
#   结果它把**真实的表名**吃掉了：`SHOW TABLES` 的输出里
#   `ods_user` / `dwd_user_detail` / `dwd_traffic_behavior_detail` 这类
#   "纯小写字母+下划线、不含数字"的名字**全部命中该模式**，
#   于是 23 张表被数成 11 张 —— 而脚本报的是"表数不对"，
#   看起来像数据问题，实际是**过滤器删掉了真数据**。
#   教训：过滤噪声要用"白名单式的已知噪声"，不要用可能命中真实数据的宽模式。
#   现在只排除已知的日志文本，且不排除任何"看起来像标识符"的行。
_LAKE_NOISE='WARN|Spark Web UI|Spark master|To adjust|Time taken|SLF4J|log4j'

lake_scalar() {
    bash "${REPO_ROOT}/scripts/spark-sql.sh" -e "$1" 2>/dev/null \
        | grep -vE "${_LAKE_NOISE}" \
        | grep -vE '^[[:space:]]*$' \
        | tail -1 | tr -d '[:space:]'
}

# 列出某个命名空间下的表名（**每行一个**）。
#
# !! 实测：`SHOW TABLES IN <ns>` 的输出**只有表名本身**，没有表头 !!
#   （用 cat -A 核对过：23 行、无 `namespace` 表头、无制表符分隔列。）
#   所以这里**只去掉空行**，不做任何"像表名就别要"的过滤。
#
# !! 这里**不能** `tr -d '[:space:]'`（第二次数错的根因）!!
#   那一句会把**换行也删掉**，23 个表名被拼成一行
#   （`ods_userods_productods_orders...`），随后按行计数得到 1 ——
#   又是一次"过滤器把真数据搞坏了"，而且症状还是"表数不对"。
#   lake_scalar 可以删空白（它只要一个标量），但列表绝对不能。
lake_tables() {
    bash "${REPO_ROOT}/scripts/spark-sql.sh" -e "$1" 2>/dev/null \
        | grep -vE "${_LAKE_NOISE}" \
        | grep -vE '^[[:space:]]*$' \
        | sed 's/[[:space:]]*$//' \
        | sort
}

# 查 Doris 标量（经 doris-fe；口令从 .env 取，不落日志）
doris_scalar() {
    local pw
    pw="$(envv DORIS_ROOT_PASSWORD)"
    docker exec -i doris-fe mysql -h 127.0.0.1 -P 9030 -uroot -p"${pw}" -B -N \
        --connect-timeout=15 -e "$1" 2>/dev/null | tail -1 | tr -d '[:space:]'
}

# ============================================================
# 1. 阶段接入自检
# ============================================================
step_stage_wiring() {
    step_start "1/8 阶段接入自检（五处清单必须一致）"

    local pipeline="${REPO_ROOT}/scripts/run-batch-pipeline.sh"
    local submit="${REPO_ROOT}/scripts/submit-offline-job.sh"
    local migrate="${REPO_ROOT}/infrastructure/spark/jobs/migrate_parquet_to_iceberg.py"
    local dag="${REPO_ROOT}/airflow/dags/offline_lakehouse_pipeline.py"

    # ---- 1.1 STAGES 数组里三个流量域分层阶段都在 ----
    local stages_line
    stages_line="$(grep -m2 '^STAGES=(' "${pipeline}" | tail -1)"
    check_true "run-batch-pipeline.sh 的 STAGES 含 traffic-dwd" "命中" \
        grep -q 'traffic-dwd' <<< "${stages_line}"
    check_true "run-batch-pipeline.sh 的 STAGES 含 traffic-dws" "命中" \
        grep -q 'traffic-dws' <<< "${stages_line}"
    check_true "run-batch-pipeline.sh 的 STAGES 含 traffic-ads" "命中" \
        grep -q 'traffic-ads' <<< "${stages_line}"
    check_true "run-batch-pipeline.sh 的 STAGES 含 traffic-reconcile" "命中" \
        grep -q 'traffic-reconcile' <<< "${stages_line}"

    # ---- 1.2 每个阶段都必须有 run_stage 的 case 分支 ----
    #
    # !! 这是本次验收最要紧的一条 !!
    #   STAGES 里有、case 里没有 = case 穿透 + rc 保持 0 = 阶段静默空转但报告成功。
    #   逐个阶段检查，而不是只挑新增的三个 —— 存量阶段同样可能被改坏。
    local stage missing=""
    for stage in ods archive dwd dws ads traffic-dwd traffic-dws traffic-ads \
                 reconcile traffic-reconcile load iceberg-migrate; do
        if ! grep -qE "^        ${stage}\)" "${pipeline}"; then
            missing="${missing} ${stage}"
        fi
    done
    check "run_stage 的 case 覆盖全部阶段" "" "${missing}"

    # ---- 1.3 兜底分支必须存在且必须失败 ----
    check_true "run_stage 兜底分支存在（未登记阶段大声失败）" "命中" \
        grep -q '没有对应的执行分支' "${pipeline}"
    check_true "兜底分支返回非 0（不能只是打印警告）" "命中 rc=1" \
        grep -q 'rc=1' "${pipeline}"

    # ---- 1.4 --list 覆盖新增阶段 ----
    local listed=""
    for stage in traffic-dwd traffic-dws traffic-ads traffic-reconcile; do
        grep -q "printf '  %-18s %s\\\\n' ${stage} " "${pipeline}" || listed="${listed} ${stage}"
    done
    check "--list 输出覆盖新增阶段" "" "${listed}"

    # ---- 1.5 submit-offline-job.sh 白名单 + 描述 + case + 兜底 ----
    local sub_missing=""
    for stage in traffic-dwd traffic-dws traffic-ads traffic-reconcile; do
        grep -q "${stage}" "${submit}" || sub_missing="${sub_missing} ${stage}"
    done
    check "submit-offline-job.sh 白名单覆盖新增阶段" "" "${sub_missing}"

    local sub_case_missing=""
    for stage in traffic-dwd traffic-dws traffic-ads traffic-reconcile; do
        grep -qE "^        ${stage}\)" "${submit}" || sub_case_missing="${sub_case_missing} ${stage}"
    done
    check "submit_stage 的 case 覆盖新增阶段" "" "${sub_case_missing}"

    check_true "submit-offline-job.sh 兜底分支存在且失败" "命中" \
        grep -q '没有对应的执行分支' "${submit}"

    # 阶段描述变量：eval 取不到会打印空说明（不报错），因此显式核对
    local desc_missing=""
    for stage in traffic_dwd traffic_dws traffic_ads traffic_reconcile; do
        grep -q "^STAGE_DESC_${stage}=" "${submit}" || desc_missing="${desc_missing} ${stage}"
    done
    check "阶段描述变量齐全" "" "${desc_missing}"

    # ---- 1.6 Iceberg 迁移清单必须含流量域 5 张表 ----
    local mig_missing=""
    for t in dwd_traffic_behavior_detail dws_traffic_overview_1d dws_traffic_funnel_1d \
             ads_traffic_1m ads_traffic_1d; do
        grep -q "\"${t}\"" "${migrate}" || mig_missing="${mig_missing} ${t}"
    done
    check "Iceberg 迁移清单含流量域 5 张表" "" "${mig_missing}"

    # ---- 1.7 DAG 必须按依赖串起来 ----
    check_true "DAG 含流量域四段任务" "命中 traffic_reconcile" \
        grep -q 'traffic_reconcile' "${dag}"
    check_true "DAG 把 traffic_reconcile 排在 load 之前" "命中" \
        grep -q 'traffic_reconcile >> load' "${dag}"

    # ---- 1.8 阶段顺序：iceberg-migrate 必须最后 ----
    #
    # !! 为什么这条要断言 !!
    #   iceberg-migrate 迁移的是"这一轮已建完整"的湖仓。
    #   若它跑到流量域前面，Iceberg 库里就会缺那 5 张表 ——
    #   而迁移作业**不会报错**（它只核对自己清单里的表）。
    local last
    last="$(grep -m2 '^STAGES=(' "${pipeline}" | tail -1 | sed 's/.*(//; s/).*//' | awk '{print $NF}')"
    check "STAGES 的最后一项是 iceberg-migrate" "iceberg-migrate" "${last}"
}

# ============================================================
# 2. 流量域 DWD
# ============================================================
step_traffic_dwd() {
    step_start "2/8 流量域 DWD（行数 / 唯一性 / 枚举 / 漏斗基线 / 与实时侧同形）"

    local dwd_rows ods_rows
    dwd_rows="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.dwd_traffic_behavior_detail;')"
    ods_rows="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ods_behavior_event;')"

    # 非空守卫：空表上的相等断言是空洞的
    check_true "ODS 行为事件非空（防空集合上的假通过）" "实际 ${ods_rows:-0} 行" \
        test "${ods_rows:-0}" -gt 0
    check "DWD 行数 == ODS 行数" "${ods_rows}" "${dwd_rows}"

    local dup
    dup="$(lake_scalar 'SELECT COUNT(*) FROM (SELECT event_id FROM lakehouse.dwd_traffic_behavior_detail GROUP BY event_id HAVING COUNT(*) > 1) t;')"
    check "event_id 主键唯一" "0" "${dup:-0}"

    local bad_types
    bad_types="$(lake_scalar "SELECT COUNT(*) FROM lakehouse.dwd_traffic_behavior_detail WHERE event_type NOT IN ('VIEW','CLICK','CART','FAVORITE','BUY');")"
    check "event_type 全在 5 类白名单内" "0" "${bad_types:-0}"

    local nulls
    nulls="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.dwd_traffic_behavior_detail WHERE event_time IS NULL OR user_id IS NULL OR event_id IS NULL;')"
    check "关键字段无空值（event_time/user_id/event_id）" "0" "${nulls:-0}"

    # 漏斗基线（与实时 DWD 逐类一致）
    local funnel
    funnel="$(lake_scalar "SELECT CONCAT_WS(',', SUM(CASE WHEN event_type='VIEW' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='CLICK' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='CART' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='BUY' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='FAVORITE' THEN 1 ELSE 0 END)) FROM lakehouse.dwd_traffic_behavior_detail;")"
    local v c ca b f
    IFS=',' read -r v c ca b f <<< "${funnel}"
    printf '     离线漏斗： VIEW=%s CLICK=%s CART=%s BUY=%s FAVORITE=%s\n' \
        "${v:-0}" "${c:-0}" "${ca:-0}" "${b:-0}" "${f:-0}"
    check_true "漏斗逐级收窄 VIEW>CLICK>CART>BUY" "${funnel}" \
        test "${v:-0}" -gt "${c:-0}" -a "${c:-0}" -gt "${ca:-0}" -a "${ca:-0}" -gt "${b:-0}"

    # 与**实时 DWD** 逐类一致 —— 这是"批流一体"在明细层的证据
    local rt_funnel
    rt_funnel="$(doris_scalar "SELECT CONCAT_WS(',', SUM(CASE WHEN event_type='VIEW' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='CLICK' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='CART' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='BUY' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type='FAVORITE' THEN 1 ELSE 0 END)) FROM ecommerce.dwd_traffic_behavior_detail;")"
    check "漏斗逐类计数 == 实时 DWD" "${rt_funnel}" "${funnel}"

    local rt_dwd_rows
    rt_dwd_rows="$(doris_scalar 'SELECT COUNT(*) FROM ecommerce.dwd_traffic_behavior_detail;')"
    check "DWD 行数 == 实时 DWD 行数" "${rt_dwd_rows}" "${dwd_rows}"

    # 字段类型（对账与下游复算的结构前提）
    local types
    types="$(bash "${REPO_ROOT}/scripts/spark-sql.sh" -e 'DESCRIBE lakehouse.dwd_traffic_behavior_detail;' 2>/dev/null \
             | grep -vE 'WARN|Spark Web UI|Spark master|To adjust|Time taken|^$|Partition|col_name|^#' \
             | awk '{gsub(/ /,"",$1); gsub(/ /,"",$2); print $1"="$2}' | sort | tr '\n' ' ')"
    printf '     DWD 字段： %s\n' "${types}"
    check_true "event_time 为 timestamp" "命中" grep -q 'event_time=timestamp' <<< "${types}"
    check_true "user_id 为 bigint" "命中" grep -q 'user_id=bigint' <<< "${types}"
    check_true "event_id 为 string" "命中" grep -q 'event_id=string' <<< "${types}"
}

# ============================================================
# 3. 流量域 DWS + ADS
# ============================================================
step_traffic_dws_ads() {
    step_start "3/8 流量域 DWS + ADS（层间闭合 / uv 口径 / 与实时侧同形）"

    local dwd_rows
    dwd_rows="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.dwd_traffic_behavior_detail;')"

    # ---- DWS 可加量闭合 ----
    local dws_pv
    dws_pv="$(lake_scalar 'SELECT SUM(pv) FROM lakehouse.dws_traffic_overview_1d;')"
    check "DWS 总览 pv 合计 == DWD" "${dwd_rows}" "${dws_pv}"

    local dws_views
    dws_views="$(lake_scalar "SELECT SUM(view_cnt) FROM lakehouse.dws_traffic_overview_1d;")"
    local dwd_views
    dwd_views="$(lake_scalar "SELECT COUNT(*) FROM lakehouse.dwd_traffic_behavior_detail WHERE event_type='VIEW';")"
    check "DWS 总览 view_cnt 合计 == DWD" "${dwd_views}" "${dws_views}"

    local dws_days dwd_days
    dws_days="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.dws_traffic_overview_1d;')"
    dwd_days="$(lake_scalar 'SELECT COUNT(*) FROM (SELECT DISTINCT TO_DATE(event_time) AS d FROM lakehouse.dwd_traffic_behavior_detail) t;')"
    check "DWS 总览天数 == DWD 出现过的日期数" "${dwd_days}" "${dws_days}"

    # ---- 漏斗表与总览表同源 ----
    local fn_rows fn_views
    fn_rows="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.dws_traffic_funnel_1d;')"
    fn_views="$(lake_scalar 'SELECT SUM(view_cnt) FROM lakehouse.dws_traffic_funnel_1d;')"
    check "漏斗表行数 == 总览表行数" "${dws_days}" "${fn_rows}"
    check "漏斗表 view_cnt 合计 == 总览" "${dws_views}" "${fn_views}"

    # ---- uv：去重口径 + 逐日精确核对 ----
    #
    # 逐日核对（而不是把全表相加）：SUM(日 uv) 与全局去重用户数必然不等，
    # 拿它们相等做断言是把两个不同定义当成同一个数。
    local uv_bad
    uv_bad="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.dws_traffic_overview_1d d LEFT JOIN (SELECT TO_DATE(event_time) AS dt, COUNT(DISTINCT user_id) AS u FROM lakehouse.dwd_traffic_behavior_detail GROUP BY TO_DATE(event_time)) t ON d.dt = t.dt WHERE d.uv <> COALESCE(t.u, 0);')"
    check "DWS 每日 uv == DWD 当日精确去重（不一致天数）" "0" "${uv_bad:-0}"

    # ---- ADS 分钟层 ----
    local ads_rows
    ads_rows="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_traffic_1m;')"
    check_true "ADS 1m 已产出（防空集合上的假通过）" "实际 ${ads_rows:-0} 个窗口" \
        test "${ads_rows:-0}" -gt 0

    local ads_pv ads_views
    ads_pv="$(lake_scalar 'SELECT SUM(pv) FROM lakehouse.ads_traffic_1m;')"
    ads_views="$(lake_scalar 'SELECT SUM(view_cnt) FROM lakehouse.ads_traffic_1m;')"
    check "ADS 1m pv 合计 == DWD" "${dwd_rows}" "${ads_pv}"
    check "ADS 1m view_cnt 合计 == DWD" "${dwd_views}" "${ads_views}"

    local bad_uv
    bad_uv="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_traffic_1m WHERE uv > pv;')"
    check "ADS 1m 无 uv > pv 的窗口" "0" "${bad_uv:-0}"

    # ---- ADS 天层 ----
    local day_pv day_views
    day_pv="$(lake_scalar 'SELECT SUM(pv) FROM lakehouse.ads_traffic_1d;')"
    day_views="$(lake_scalar 'SELECT SUM(view_cnt) FROM lakehouse.ads_traffic_1d;')"
    check "ADS 1d pv == 1m 按天上卷" "${ads_pv}" "${day_pv}"
    check "ADS 1d view_cnt == 1m 按天上卷" "${ads_views}" "${day_views}"

    local day_uv_bad
    day_uv_bad="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_traffic_1d d LEFT JOIN (SELECT TO_DATE(event_time) AS dt, COUNT(DISTINCT user_id) AS u FROM lakehouse.dwd_traffic_behavior_detail GROUP BY TO_DATE(event_time)) t ON d.dt = t.dt WHERE d.uv <> COALESCE(t.u, 0);')"
    check "ADS 1d 每日 uv == DWD 当日精确去重（不一致天数）" "0" "${day_uv_bad:-0}"

    # ---- 与实时 ADS 同形（字段名 + 顺序） ----
    #
    # !! 分区列 dt 必须先剔除 !!
    #   DESCRIBE 会把分区列列出来（dt 在最后），而实时表
    #   ecommerce.ads_realtime_traffic_1m 只有 12 个数据列，没有分区列。
    #   不剔除就等于要求实时表也有一个它本来就没有的列 —— 实测踩过：
    #   作业里的同形断言第一次跑就是这样"失败"的，而数据其实完全正确。
    local ads_cols rt_cols
    ads_cols="$(bash "${REPO_ROOT}/scripts/spark-sql.sh" -e 'DESCRIBE lakehouse.ads_traffic_1m;' 2>/dev/null \
               | grep -vE 'WARN|Spark Web UI|Spark master|To adjust|Time taken|^$|Partition|col_name|^#' \
               | awk '{gsub(/ /,"",$1); print $1}' | grep -v '^dt$' | tr '\n' ',' )"
    rt_cols="$(doris_scalar "SELECT GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) FROM information_schema.columns WHERE TABLE_SCHEMA='ecommerce' AND TABLE_NAME='ads_realtime_traffic_1m';" | tr 'A-Z' 'a-z')"
    check "ADS 1m 数据列与实时表逐列同序（不含分区列 dt）" "${rt_cols}," "${ads_cols}"
}

# ============================================================
# 4. Iceberg 侧核对
# ============================================================
step_iceberg() {
    step_start "4/8 Iceberg 侧核对（23 张表 / Provider / 行数与 Parquet 侧逐表一致）"

    local ice_tables
    ice_tables="$(lake_tables 'SHOW TABLES IN iceberg.lakehouse_iceberg;')"

    # ---- 4.1 逐个表名核对（**主判据**），数量相等只作为副产品 ----
    #
    # !! 为什么断言用"逐个表名都在"而不是"数量 == 23" !!
    #   本脚本在这上面连错两次（11/7，然后 1/0），两次都是
    #   **解析**问题而不是数据问题，而症状全都表现为"表数不对" ——
    #   看起来像数据缺失，实际是过滤器把真数据吃掉了。
    #   数量断言对输出格式很敏感；逐个表名核对则：
    #     ① 不怕输出格式变化（只要表名还在一行里就能匹配）；
    #     ② 失败信息直接给出**缺哪张表**，而不是一个孤零零的数字；
    #     ③ 数量相等成了副产品：23 个都在，数量自然就是 23。
    #   这正是"判据要能定位问题"的写法。
    local EXPECTED_ICEBERG_TABLES=(
        # 交易域 18 张（Sprint 5 阶段 4 迁移）
        ods_user ods_product ods_orders ods_payment ods_refund ods_behavior_event
        dwd_user_detail dwd_product_detail dwd_trade_order_detail
        dwd_trade_payment_detail dwd_trade_refund_detail
        dws_trade_overview_1d dws_trade_category_1d dws_trade_user_1d
        ads_batch_trade_1m ads_batch_trade_1d ads_batch_category_1m ads_batch_category_1d
        # 流量域 5 张（Sprint 5 阶段 5 新增）
        dwd_traffic_behavior_detail dws_traffic_overview_1d dws_traffic_funnel_1d
        ads_traffic_1m ads_traffic_1d
    )

    local missing=() t
    for t in "${EXPECTED_ICEBERG_TABLES[@]}"; do
        printf '%s\n' "${ice_tables}" | grep -qx "${t}" || missing+=("${t}")
    done

    local found=$(( ${#EXPECTED_ICEBERG_TABLES[@]} - ${#missing[@]} ))
    check "Iceberg 库含全部 23 张期望表（缺 ${#missing[@]} 张）" "23" "${found}"
    if [ "${#missing[@]}" -gt 0 ]; then
        printf '     !! 缺失的表： %s\n' "${missing[*]}"
    fi

    # 反向：库里不能有**清单之外**的表（多出来的表同样是偏差，
    # 例如某次误建的表会一直留在库里，而"期望表都在"这种断言抓不到它）
    local extra=() name
    while IFS= read -r name; do
        [ -n "${name}" ] || continue
        local known=0 e
        for e in "${EXPECTED_ICEBERG_TABLES[@]}"; do
            [ "${e}" = "${name}" ] && known=1 && break
        done
        [ "${known}" -eq 1 ] || extra+=("${name}")
    done <<< "${ice_tables}"
    check "Iceberg 库无清单之外的表" "0" "${#extra[@]}"
    if [ "${#extra[@]}" -gt 0 ]; then
        printf '     !! 多出的表： %s\n' "${extra[*]}"
    fi

    printf '     Iceberg 库表数（实际行数）： %s\n' \
        "$(printf '%s\n' "${ice_tables}" | grep -cE '^[a-z]')"

    # 逐表行数与 Parquet 侧一致（含流量域 5 张表）
    for t in dwd_traffic_behavior_detail dws_traffic_overview_1d dws_traffic_funnel_1d \
             ads_traffic_1m ads_traffic_1d; do
        local p i
        p="$(lake_scalar "SELECT COUNT(*) FROM lakehouse.${t};")"
        i="$(lake_scalar "SELECT COUNT(*) FROM iceberg.lakehouse_iceberg.${t};")"
        check "${t} 行数 Parquet == Iceberg" "${p}" "${i}"
    done

    # 交易域的 18 张表仍在（回归：迁移成果未被破坏）
    #
    # !! 计数必须只数"确实出现在清单里的表"，不能用 wc -w 除以空格 !!
    #   这里逐个 grep -xqw（整行精确匹配）后累加，避免子串误命中
    #   （例如 ads_traffic_1m 是 ads_traffic_1m 的前缀，但 ods_user 与
    #     dwd_user_detail 之间没有前缀关系 —— 逐行精确匹配最稳）。
    # 交易域 18 张表已在上面"逐个表名核对"里覆盖（含在 EXPECTED_ICEBERG_TABLES 中），
    # 不再单独数一遍 —— 同一件事两处断言会有两处漂移的可能。

    # Provider 必须是 iceberg（不是 parquet/hive）——
    # 这是"表真的迁过去了"的直接证据，行数相同也可能是两张不同的表。
    #
    # !! 不能写成 `SELECT ... FROM (DESCRIBE EXTENDED t) WHERE ...` !!
    #   实测：Spark SQL **不支持把 DESCRIBE 当子查询**，报
    #     ParseException: Syntax error at or near '('
    #   这里改为从 DESCRIBE 的**输出行**里取 Provider 那一行的值。
    #   取法：DESCRIBE EXTENDED 的输出是 `col_name \t data_type` 两列，
    #   找到 col_name 为 Provider 的那一行，取第二列。
    local provider
    provider="$(bash "${REPO_ROOT}/scripts/spark-sql.sh" -e \
        'DESCRIBE EXTENDED iceberg.lakehouse_iceberg.ads_traffic_1m;' 2>/dev/null \
        | grep -vE "${_LAKE_NOISE}" \
        | awk -F'\t' '$1 ~ /Provider/ { gsub(/[[:space:]]/, "", $2); print $2 }' | head -1)"
    check "ads_traffic_1m 的 Provider" "iceberg" "${provider:-<未取到>}"

    # 时间旅行：Iceberg 相对 Parquet 的核心增益，必须有实证。
    # 快照元数据表可查（`<表>.snapshots`），且 VERSION AS OF 能真的取到数据。
    local snapshots
    snapshots="$(lake_scalar 'SELECT COUNT(*) FROM iceberg.lakehouse_iceberg.ads_traffic_1m.snapshots;')"
    check_true "支持时间旅行（快照表可查，快照数 > 0）" "实际 ${snapshots:-0} 个快照" \
        test "${snapshots:-0}" -gt 0

    local old_snapshot
    old_snapshot="$(lake_scalar 'SELECT MIN(snapshot_id) FROM iceberg.lakehouse_iceberg.ads_traffic_1m.snapshots;')"
    if [ -n "${old_snapshot}" ]; then
        local tt_rows expect_rows
        tt_rows="$(lake_scalar "SELECT COUNT(*) FROM iceberg.lakehouse_iceberg.ads_traffic_1m VERSION AS OF ${old_snapshot};")"
        # 不跨函数复用局部变量（会静默取到空值），这里重新查一次
        expect_rows="$(lake_scalar 'SELECT COUNT(*) FROM iceberg.lakehouse_iceberg.ads_traffic_1m;')"
        check "VERSION AS OF 最早快照可查（行数 == 当前快照）" "${expect_rows}" "${tt_rows:-0}"
    else
        printf '  %b %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "取不到快照 id，跳过 VERSION AS OF 演练"
        SKIP=$(( SKIP + 1 ))
    fi
}

# ============================================================
# 5. 流量域批流对账（本 Sprint 的核心结论）
# ============================================================
step_traffic_reconcile() {
    step_start "5/8 流量域批流对账（逐窗口全量比对）"

    local rows
    rows="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_traffic_1m;')"
    check_true "逐窗口对账表已产出（防空集合上的假通过）" "实际 ${rows:-0} 行" \
        test "${rows:-0}" -gt 0

    local mismatch
    mismatch="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_traffic_1m WHERE is_match = false;')"
    check "不一致窗口数" "0" "${mismatch:-0}"

    # 单边窗口：实时有离线无 / 离线有实时无
    local rt_only bt_only
    rt_only="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_traffic_1m WHERE realtime_uv IS NULL;')"
    bt_only="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_traffic_1m WHERE batch_uv IS NULL;')"
    check "仅实时侧有的窗口数（离线漏算）" "0" "${rt_only:-0}"
    check "仅离线侧有的窗口数（离线多算）" "0" "${bt_only:-0}"

    # 最新批次的汇总结论
    local summary
    summary="$(lake_scalar 'SELECT CONCAT_WS("|", batch_windows, realtime_windows, matched_windows, mismatched_windows, is_pass) FROM lakehouse.ads_reconcile_traffic_summary ORDER BY compared_at DESC LIMIT 1;')"
    local bw rw mw mm ip
    IFS='|' read -r bw rw mw mm ip <<< "${summary}"
    printf '     最新批次： 离线 %s 窗口 / 实时 %s 窗口 / 一致 %s / 不一致 %s / is_pass=%s\n' \
        "${bw:-?}" "${rw:-?}" "${mw:-?}" "${mm:-?}" "${ip:-?}"
    check "最新批次 is_pass" "true" "${ip:-}"
    check "最新批次不一致窗口数" "0" "${mm:-}"
    check_true "对账窗口数 > 0（防空区间假通过）" "实际 ${bw:-0} 个" \
        test "${bw:-0}" -gt 0

    # 全量覆盖：对账窗口数 == 两侧窗口总数（区间尾部留了 3 分钟安全边界，
    # 所以允许离线/实时各自少最后几行，但两侧的总数必须与汇总里的数吻合）
    local offline_total
    offline_total="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_traffic_1m;')"
    check_true "对账窗口数 <= 离线窗口总数" "对账 ${bw:-0} vs 离线 ${offline_total:-0}" \
        test "${bw:-0}" -le "${offline_total:-0}"

    local rt_total
    rt_total="$(doris_scalar 'SELECT COUNT(*) FROM ecommerce.ads_realtime_traffic_1m;')"
    printf '     实时侧窗口总数： %s（离线 %s）\n' "${rt_total:-?}" "${offline_total:-?}"

    # 合计可比：PV 是可加指标，两侧合计必须精确相等
    local rt_pv bt_pv
    rt_pv="$(doris_scalar 'SELECT SUM(pv) FROM ecommerce.ads_realtime_traffic_1m;')"
    bt_pv="$(lake_scalar 'SELECT SUM(pv) FROM lakehouse.ads_traffic_1m;')"
    check "PV 合计：实时 == 离线（可加指标）" "${rt_pv}" "${bt_pv}"

    # 逐类行为计数合计也相等
    local rt_v bt_v
    rt_v="$(doris_scalar 'SELECT SUM(view_cnt) FROM ecommerce.ads_realtime_traffic_1m;')"
    bt_v="$(lake_scalar 'SELECT SUM(view_cnt) FROM lakehouse.ads_traffic_1m;')"
    check "VIEW 合计：实时 == 离线" "${rt_v}" "${bt_v}"
}

# ============================================================
# 6. 对账证据与口径
# ============================================================
step_evidence() {
    step_start "6/8 对账证据与口径（区间可核 / 去重不可加 / 比率列独立核对）"

    # ---- 6.1 区间与边界可核（落在汇总表里，不是只在日志里） ----
    local scope
    scope="$(lake_scalar 'SELECT CONCAT_WS("|", scope_start, scope_end, realtime_total_pv, batch_total_pv) FROM lakehouse.ads_reconcile_traffic_summary ORDER BY compared_at DESC LIMIT 1;')"
    printf '     对账区间与合计： %s\n' "${scope}"
    local rs re rtp btp
    IFS='|' read -r rs re rtp btp <<< "${scope}"
    check_true "区间起点非空" "${rs}" test -n "${rs}"
    check "区间内两侧 PV 合计一致" "${rtp}" "${btp}"

    # ---- 6.2 去重指标不可加：实证 ----
    #
    # 这条断言的价值在于"证明 uv 确实是去重口径"：
    # 若某处误把逐窗口 uv 上卷，SUM(窗口 uv) 会退化成一个比全局去重
    # 大得多的数（实测 19999 vs 1199）。反过来，
    # 若 uv 被算成"窗口内事件数"，SUM(窗口 uv) 就会等于 PV。
    local win_uv_sum global_uv total_pv
    win_uv_sum="$(lake_scalar 'SELECT SUM(uv) FROM lakehouse.ads_traffic_1m;')"
    global_uv="$(lake_scalar 'SELECT COUNT(DISTINCT user_id) FROM lakehouse.dwd_traffic_behavior_detail;')"
    total_pv="$(lake_scalar 'SELECT SUM(pv) FROM lakehouse.ads_traffic_1m;')"
    printf '     SUM(窗口 uv)=%s  全局去重用户数=%s  总 PV=%s\n' \
        "${win_uv_sum:-?}" "${global_uv:-?}" "${total_pv:-?}"
    check_true "SUM(窗口 uv) > 全局去重用户数（证明 uv 是去重口径，不是可加量）" \
        "${win_uv_sum:-0} vs ${global_uv:-0}" \
        test "${win_uv_sum:-0}" -gt "${global_uv:-0}"
    check_true "SUM(窗口 uv) <= 总 PV（uv 不可能大于 pv）" \
        "${win_uv_sum:-0} vs ${total_pv:-0}" \
        test "${win_uv_sum:-0}" -le "${total_pv:-0}"

    # ---- 6.3 比率列：换成"每一侧比率 == 由该侧自身计数按公式重算"的独立判据 ----
    #
    # !! 为什么不是"两侧比率相等" !!
    #   比率是派生量，判据列（uv/pv/6 个计数）逐窗口相等时它数学上必然相等，
    #   拿"两侧比率相等"当判据既不增加信息，又会被两侧除法实现细节的
    #   末位差异误报（假差异）。
    #   真正的判据是"每一侧的比率是否等于由它自己的计数算出的值" ——
    #   这条更根本：两侧一起错成同一个值它也能抓出来。
    #
    # !! 实测结论（本项目真实数据）!!
    #   离线侧 0 个矛盾窗口；实时侧 **1** 个（2026-03-21 19:23:00，
    #   view_cnt=2 / click_cnt=1 / click_rate=0.0000，按公式应为 0.5000）。
    #   1/2 与 0/2 的差**不在末位小数**，是实时链路那一行自相矛盾 ——
    #   属于**真实数据缺陷**，必须如实记录，不许放宽容差蒙过去。
    #   因此这里：
    #     - 离线侧断言为 0（我方可控的那一半，必须是 0）；
    #     - 实时侧只**记录并打印**，不计入失败 —— 缺陷在实时链路，
    #       离线验收脚本不该替它背锅（重跑离线一万次也改不了那个值）。
    local rt_anom bt_anom
    rt_anom="$(lake_scalar 'SELECT realtime_rate_anomaly_windows FROM lakehouse.ads_reconcile_traffic_summary ORDER BY compared_at DESC LIMIT 1;')"
    bt_anom="$(lake_scalar 'SELECT batch_rate_anomaly_windows FROM lakehouse.ads_reconcile_traffic_summary ORDER BY compared_at DESC LIMIT 1;')"
    check "离线侧比率与自身计数矛盾的窗口数" "0" "${bt_anom:-?}"
    printf '  %b %-52s %s\n' "${C_YELLOW}[NOTE]${C_RESET}" \
        "实时侧比率与自身计数矛盾的窗口数（已知缺陷，见文档）" "${rt_anom:-?}"

    # 两侧比率列逐窗口的实际取值差异（作为上面那条结论的量化旁证）
    local rate_diff
    rate_diff="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_traffic_1m WHERE COALESCE(diff_click_rate,0) <> 0 OR COALESCE(diff_cart_rate,0) <> 0 OR COALESCE(diff_buy_rate,0) <> 0 OR (realtime_click_rate IS NULL) <> (batch_click_rate IS NULL) OR (realtime_cart_rate IS NULL) <> (batch_cart_rate IS NULL) OR (realtime_buy_rate IS NULL) <> (batch_buy_rate IS NULL);')"
    printf '  %b %-52s %s\n' "${C_YELLOW}[NOTE]${C_RESET}" \
        "两侧比率列取值不同的窗口数（= 上面那个实时缺陷）" "${rate_diff:-?}"

    # 关键交叉验证：矛盾窗口的**计数**两侧必须一致 ——
    # 若计数也不同，问题就升级成主判据失败，而不是派生列缺陷。
    local anom_count_mismatch
    anom_count_mismatch="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_traffic_1m WHERE realtime_rate_anomaly AND NOT is_match;')"
    check "比率矛盾窗口的计数列仍然一致（否则升级为主判据失败）" "0" "${anom_count_mismatch:-0}"

    # ---- 6.4 汇总表每个批次的主判据结论都必须是 pass ----
    local bad_batches
    bad_batches="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_reconcile_traffic_summary WHERE is_pass = false;')"
    check "历史批次里没有主判据失败结论（否则是放宽判据或漏看）" "0" "${bad_batches:-0}"

    # ---- 6.5 服务层已装载（Doris 侧可查，供看板/Agent 使用） ----
    local doris_rows
    doris_rows="$(doris_scalar 'SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1m;')"
    local lake_rows
    lake_rows="$(lake_scalar 'SELECT COUNT(*) FROM lakehouse.ads_traffic_1m;')"
    check "Doris lakehouse_ads.ads_traffic_1m 行数 == 湖仓" "${lake_rows}" "${doris_rows}"

    local doris_trade
    doris_trade="$(doris_scalar 'SELECT COUNT(*) FROM lakehouse_ads.ads_reconcile_traffic_1m;')"
    check_true "Doris 侧流量域对账表已装载" "实际 ${doris_trade:-0} 行" \
        test "${doris_trade:-0}" -gt 0

    # ---- 6.6 只读账号可查（Sprint 6/7 的服务层依赖） ----
    local ro_pw ro_count
    ro_pw="$(envv API_DORIS_PASSWORD)"
    if [ -n "${ro_pw}" ]; then
        ro_count="$(docker exec -i doris-fe mysql -h 127.0.0.1 -P 9030 -uagent_ro -p"${ro_pw}" -B -N \
                    --connect-timeout=15 -e 'SELECT COUNT(*) FROM lakehouse_ads.ads_traffic_1m;' 2>/dev/null | tail -1 | tr -d '[:space:]')"
        check "只读账号 agent_ro 可查流量域表" "${lake_rows}" "${ro_count:-0}"
    else
        printf '  %b %s\n' "${C_YELLOW}[SKIP]${C_RESET}" ".env 缺少 API_DORIS_PASSWORD，跳过只读账号核对"
        SKIP=$(( SKIP + 1 ))
    fi
}

# ============================================================
# 7. 内存与执行方式
# ============================================================
step_memory() {
    step_start "7/8 内存与执行方式（错峰模式 / 闸门未被放宽）"

    local threshold
    threshold="$(grep -E '^MIN_AVAILABLE_MB_FOR_BATCH=' "${REPO_ROOT}/scripts/lib/memory-guard.sh" \
                 | head -1 | sed 's/.*:-\([0-9]*\).*/\1/')"
    check "批处理内存闸门阈值未被放宽" "3000" "${threshold:-0}"

    check_true "闸门在内存不足时会拒绝（require_memory_for_batch 返回非 0）" "命中" \
        grep -q 'return 1' "${REPO_ROOT}/scripts/lib/memory-guard.sh"

    check_true "实时链路容器清单含 flink-taskmanager" "命中" \
        grep -q 'REALTIME_STACK_CONTAINERS=.*flink-taskmanager' "${REPO_ROOT}/scripts/lib/memory-guard.sh"

    # 新增阶段必须都走错峰模式：它们只能在 run-batch-pipeline.sh 里被派发，
    # 而 batch-mode.sh 是那个入口的错峰包壳（暂停 Flink → 跑批 → 恢复）。
    # 这里断言两件事：batch-mode 确实调用 run-batch-pipeline，且流量域阶段
    # 只出现在 run-batch-pipeline 的 case 里（没有另起一条绕过闸门的路径）。
    check_true "batch-mode.sh 经 run-batch-pipeline.sh 派发（闸门在脚本里）" "命中" \
        grep -q 'run-batch-pipeline.sh' "${REPO_ROOT}/scripts/batch-mode.sh"
    check_true "batch-mode.sh 透传 --stage 参数" "命中" \
        grep -q -- '--stage' "${REPO_ROOT}/scripts/batch-mode.sh"

    # 实时链路当前必须已经恢复（批处理不应把看板永久留在暂停状态）
    local flink_running
    flink_running="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -c flink-taskmanager || true)"
    printf '     当前宿主机内存： '
    free -m | awk 'NR==2{printf "可用 %s MB / 共 %s MB\n", $7, $2}'
    check_true "实时链路已恢复（flink-taskmanager 在运行）" "实际 ${flink_running} 个" \
        test "${flink_running:-0}" -ge 1

    # 注意：这里**不再**顺带跑一次 health-check。
    # 原来步骤 7 与步骤 8 各调一次 health-check.sh（各约 7~8 秒，其中包含
    # 等待 Flink 作业恢复的就绪轮询）—— 同一件事做两遍，且第一次的结果
    # 只被打印、从不参与断言。现在统一放到第 8 步：只跑一次，且**有断言**。
    printf '     （健康检查的断言在第 8 步统一做，避免同一件事跑两遍）\n'
}

# ============================================================
# 8. 回归（轻量）
#
# !! 本步骤**故意不做整链回归**，移交关系写在文件头第 8 条说明里 !!
#   整链（含离线批处理）的验收命令：
#     bash scripts/verify-sprint-3.sh     交易域分层 + 批流对账
#     bash scripts/verify-sprint-4.sh     Airflow 调度 + 流量域归档
#     bash scripts/verify-sprint-12.sh    统一回归入口（Sprint 12 起）
#   本步骤只保留"本 Sprint 的改动有没有把已验收的东西弄坏"所必需的最短链路。
# ============================================================
step_regression() {
    step_start "8/8 回归（轻量：健康检查 / 交易域对账 / 服务可达 / 服务 active）"

    # ---- 0. 先把移交关系打印出来（不许悄悄降低覆盖面）----
    printf '  %b %s\n' "${C_YELLOW}[移交]${C_RESET}" \
        "整链回归（含离线批处理）已移交： bash scripts/verify-sprint-3.sh / verify-sprint-4.sh"
    printf '  %b %s\n' "${C_YELLOW}[移交]${C_RESET}" \
        "统一回归入口（Sprint 12 起）：    bash scripts/verify-sprint-12.sh"
    printf '  %b %s\n' "${C_YELLOW}[说明]${C_RESET}" \
        "本步骤只做轻量断言，不再重跑 3/4 的整链验收 —— 那会把单项验收放大到 25 分钟以上"

    # ---- 1. 健康检查（容器五件套 + 实时链路，共 11 项）----
    #
    # 这里就**不**再单独跑一次 grep -c 了：直接解析本步骤这一次运行的输出，
    # 既拿到断言，又能把数字打印出来（同一件事只做一次）。
    local hc_out hc_rc=0 hc_ok
    hc_out="$(bash "${REPO_ROOT}/scripts/health-check.sh" 2>&1)" || hc_rc=$?
    hc_ok="$(printf '%s\n' "${hc_out}" | grep -c '\[OK\]' || true)"
    printf '     health-check.sh： [OK] %s 项，退出码 %s\n' "${hc_ok:-0}" "${hc_rc}"
    check "health-check.sh 通过（退出码 0）" "0" "${hc_rc}"
    check "health-check [OK] 项数（Sprint 1 基线）" "11" "${hc_ok:-0}"

    # ---- 2. 交易域对账成果必须还在（Sprint 3 的核心结论）----
    local trade_summary
    trade_summary="$(lake_scalar 'SELECT CONCAT_WS("|", COUNT(*), SUM(CASE WHEN is_match THEN 0 ELSE 1 END)) FROM lakehouse.ads_reconcile_trade_1m;')"
    local trade_rows trade_bad
    IFS='|' read -r trade_rows trade_bad <<< "${trade_summary}"
    check_true "交易域对账表仍有数据（Sprint 3 成果仍在）" "实际 ${trade_rows:-0} 行" \
        test "${trade_rows:-0}" -gt 0
    check "交易域对账不一致窗口数" "0" "${trade_bad:-0}"

    # ---- 3. 服务层两个入口可达 ----
    #
    # !! 判据为什么是"2xx/3xx"而不是写死 200 !!
    #   站点协议由 .env 的 SITE_SCHEME 决定（见 AGENTS.md 15.7 硬规范 1）。
    #   哪天重新启用 TLS，这两个入口会给出 **301/302**；
    #   写死 200 会把一次**配置变更**报成**功能缺陷**（Sprint 7 踩过同类坑）。
    local code
    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 15 "$(site_base)/data/" 2>/dev/null || echo 000)"
    check_true "GET /data/ 可达（Sprint 6 未被破坏）" "实际 HTTP ${code}" \
        test "${code}" -ge 200 -a "${code}" -lt 400

    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 15 "$(site_base)/data/agent/health" 2>/dev/null || echo 000)"
    check_true "GET /data/agent/health 可达（Sprint 7 未被破坏）" "实际 HTTP ${code}" \
        test "${code}" -ge 200 -a "${code}" -lt 400

    # ---- 4. 关键服务 active ----
    #
    # 为什么用循环列清单而不是写死某一个：这几个单元分属不同 Sprint
    #   （api=Sprint 6、agent=Sprint 7、mcp=Sprint 10）。
    #   这样"谁掉了"在输出里一眼可见，而不是只报一个笼统的失败。
    local unit down=""
    for unit in data-platform-api data-platform-agent data-platform-mcp nginx; do
        if systemctl is-active --quiet "${unit}" 2>/dev/null; then
            printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "服务 active： ${unit}" "active"
            PASS=$(( PASS + 1 ))
        else
            printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "服务 active： ${unit}" "非 active"
            DETAILS+=("systemd 单元 ${unit} 不是 active")
            FAIL=$(( FAIL + 1 ))
            down="${down} ${unit}"
        fi
    done

    # ---- 5. 自动化测试（单元，毫秒级；留着是因为它便宜且直接守着业务口径）----
    local py=""
    if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
        py="${REPO_ROOT}/.venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    fi
    if [ -z "${py}" ]; then
        printf '  %b %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "找不到可用的 Python，跳过单元测试"
        SKIP=$(( SKIP + 1 ))
    elif ( cd "${REPO_ROOT}" && "${py}" -m pytest -m unit -q >/tmp/vs5-unit.log 2>&1 ); then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "单元测试通过" "$(tail -1 /tmp/vs5-unit.log | tr -d '\n')"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "单元测试失败" "见 /tmp/vs5-unit.log"
        DETAILS+=("单元测试失败")
        FAIL=$(( FAIL + 1 ))
    fi

    if [ -n "${down}" ]; then
        printf '     !! 非 active 的服务： %s\n' "${down}"
    fi
}

# ============================================================
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 5 验收：Iceberg Lakehouse + 流量域分层与对账${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    cd "${REPO_ROOT}"

    step_stage_wiring
    step_record "阶段接入自检" "$([ "${FAIL}" -eq 0 ] && echo PASS || echo FAIL)"
    local f1="${FAIL}"

    step_traffic_dwd
    step_record "流量域 DWD" "$([ "${FAIL}" -eq "${f1}" ] && echo PASS || echo FAIL)"
    local f2="${FAIL}"

    step_traffic_dws_ads
    step_record "流量域 DWS+ADS" "$([ "${FAIL}" -eq "${f2}" ] && echo PASS || echo FAIL)"
    local f3="${FAIL}"

    step_iceberg
    step_record "Iceberg 侧核对" "$([ "${FAIL}" -eq "${f3}" ] && echo PASS || echo FAIL)"
    local f4="${FAIL}"

    step_traffic_reconcile
    step_record "流量域批流对账" "$([ "${FAIL}" -eq "${f4}" ] && echo PASS || echo FAIL)"
    local f5="${FAIL}"

    step_evidence
    step_record "对账证据与口径" "$([ "${FAIL}" -eq "${f5}" ] && echo PASS || echo FAIL)"
    local f6="${FAIL}"

    step_memory
    step_record "内存与执行方式" "$([ "${FAIL}" -eq "${f6}" ] && echo PASS || echo FAIL)"
    local f7="${FAIL}"

    step_regression
    step_record "回归（轻量）" "$([ "${FAIL}" -eq "${f7}" ] && echo PASS || echo FAIL)"

    printf '\n%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 5 验收汇总${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '  通过 %s   失败 %s   跳过 %s\n' "${PASS}" "${FAIL}" "${SKIP}"

    local i
    for i in "${!STEP_NAMES[@]}"; do
        case "${STEP_RESULTS[$i]}" in
            PASS) printf '  %b  %s\n' "${C_GREEN}[PASS]${C_RESET}" "${STEP_NAMES[$i]}" ;;
            SKIP) printf '  %b  %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "${STEP_NAMES[$i]}" ;;
            *)    printf '  %b  %s\n' "${C_RED}[FAIL]${C_RESET}" "${STEP_NAMES[$i]}" ;;
        esac
    done

    if [ "${FAIL}" -gt 0 ]; then
        printf '\n%b\n' "${C_RED}失败明细：${C_RESET}"
        local d
        for d in "${DETAILS[@]}"; do printf '  - %s\n' "${d}"; done
    fi

    printf '\n'
    if [ "${FAIL}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} Sprint 5 验收通过${C_RESET}"
        printf '\n'
        printf '  流量域对账： %s\n' \
            "$(lake_scalar 'SELECT CONCAT(batch_windows, " 个窗口，不一致 ", mismatched_windows, " 个") FROM lakehouse.ads_reconcile_traffic_summary ORDER BY compared_at DESC LIMIT 1;')"
        printf '  逐窗口差异： lakehouse.ads_reconcile_traffic_1m\n'
        printf '  服务层可查： lakehouse_ads.ads_traffic_1m / ads_traffic_1d\n'
        printf '  看板：       %s/data/\n' "$(site_base)"
    else
        printf '%b\n' "${C_RED}${C_BOLD} Sprint 5 验收未通过，请按上方提示排查${C_RESET}"
        printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
        return 1
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
    return 0
}

main "$@" < /dev/null
exit $?
