#!/usr/bin/env bash
# ============================================================
# Sprint 1 — Doris Routine Load 初始化
# ============================================================
#
# 作用：把 Flink 写入 Kafka 的指标 topic 持续导入 Doris 对应表。
#
# 为什么用 Routine Load 而不是 Flink 直写 Doris：
#   见 docs/sprint/SPRINT_1.md 第 2.1 节 —— 避免依赖
#   doris-flink-connector（Doris/Flink/connector 三方版本绑定），
#   改用 Doris 内置能力：零额外依赖、自带断点续传与 exactly-once。
#
# 幂等性：CREATE ROUTINE LOAD 不支持 IF NOT EXISTS，重复创建会报
#   "job already exists"，因此这里**先查后建**，可安全重复执行。
#
# !! 实测踩坑记录（务必保留这些约束） !!
#   1) **本脚本必须在容器内运行**：宿主机没有 mysql 客户端，
#      在宿主机执行会导致列清单为空 → COLUMNS() 与 jsonpaths=[]
#      → 报 `mismatched input '$' expecting {')', ','}`。
#   2) **用 DESC 取列而不是 information_schema + GROUP_CONCAT**：
#      Doris 4.1.4 下 GROUP_CONCAT 取列返回空。
#   3) **SQL 写成文件后重定向给 mysql，不要用 mysql -e**：
#      -e 会把 `\$` 当成命令，报 `Unknown command '\$'`。
#   4) **SHOW ALL ROUTINE LOAD 需要库上下文**，必须先 USE <db>。
#
# 执行时机：由 BE 容器初始化流程在 DDL 之后调用。
# 环境变量：
#   FE_HOST —— FE 地址（默认 127.0.0.1，容器内由保活包装注入）
# ============================================================
set -uo pipefail

FE_HOST="${FE_HOST:-127.0.0.1}"
QUERY_PORT="${FE_QUERY_PORT:-9030}"
BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
DB="ecommerce"
MAX_INTERVAL="30"

mysql_q() {
    mysql -h "${FE_HOST}" -P "${QUERY_PORT}" -uroot --connect-timeout=15 "$@"
}

log()  { printf '[doris-routine-load] %s\n' "$*"; }

# ------------------------------------------------------------
# 等待 FE 可查询
# ------------------------------------------------------------
log "等待 FE (${FE_HOST}:${QUERY_PORT}) 就绪 ..."
ready=0
for attempt in $(seq 1 30); do
    if mysql_q -N -B -e "SELECT 1;" >/dev/null 2>&1; then
        ready=1
        log "FE 已就绪（第 ${attempt} 次尝试）"
        break
    fi
    sleep 5
done
if [ "${ready}" -ne 1 ]; then
    log "ERROR: FE 在 150 秒内不可查询"
    exit 1
fi

# ------------------------------------------------------------
# 创建（或跳过）单个 Routine Load
# ------------------------------------------------------------
ensure_job() {
    local job_name="$1"
    local table_name="$2"
    local topic="$3"

    local existing
    existing=$(mysql_q -N -B -e "USE ${DB}; SHOW ROUTINE LOAD WHERE Name = '${job_name}';" 2>/dev/null || true)
    if [ -n "${existing}" ]; then
        local state
        state=$(printf '%s\n' "${existing}" | awk -F'\t' '{print $8}' | head -1)
        log "已存在，跳过：${job_name}（State=${state}）"
        return 0
    fi

    # 用 DESC 取列（顺序与 schema 一致）
    local cols
    cols=$(mysql_q -N -B -e "DESC ${DB}.${table_name};" 2>/dev/null \
           | awk -F'\t' 'NF>0 && $1!="" {printf "%s%s", (n++?",":""), $1}')

    if [ -z "${cols}" ]; then
        log "  !! 取不到 ${table_name} 的列清单（表不存在？），跳过"
        return 1
    fi

    # 构造 jsonpaths：["$.col1","$.col2",...]
    #
    # !! 转义要点（实测踩坑） !!
    #   这里的 \\\" 与 \\$ 是**有意的三重转义**：
    #     第 1 层：本脚本自身（容器内 bash）
    #     第 2 层：heredoc 展开
    #     第 3 层：写入 SQL 文本时需保留 \" 与 $ 字面量
    #   Doris 的 jsonpaths 值必须是合法 JSON 数组字符串，
    #   即  "jsonpaths" = "[\"$.a\",\"$.b\"]"；
    #   若写成  "jsonpaths" = "["$.a","$.b"]" 会报
    #     mismatched input '$' expecting {')', ','}
    #   （曾因少一层转义而反复失败）。
    local paths="[" first=1 col
    for col in $(printf '%s' "${cols}" | tr ',' ' '); do
        if [ "${first}" -eq 1 ]; then
            paths="${paths}\\\"\\\$.${col}\\\""
            first=0
        else
            paths="${paths},\\\"\\\$.${col}\\\""
        fi
    done
    paths="${paths}]"

    # 写文件（不用 mysql -e，规避 \$ 转义问题）
    #
    # !! strip_outer_array 必须为 false（实测踩坑） !!
    #   Flink 的 Kafka sink 对每条记录写出**一个独立的 JSON 对象**：
    #     {"order_id":10001,"amount":280.0,...}
    #   而 strip_outer_array='true' 会让 Doris 期望整个消息是一个 JSON
    #   数组（[ {...}, {...} ]），于是每一行都解析失败，报：
    #     Reason: JSON data is not an array-object,
    #             `strip_outer_array` must be FALSE
    #   症状是 Routine Load 的 errorRows 等于 totalRows 并被 PAUSED
    #   （errCode = 102）。
    #   注意：错误详情只能通过 ErrorLogUrls 指向的 BE 接口看到，
    #   SHOW ROUTINE LOAD 本身不显示具体原因。
    local sqlfile="/tmp/rl_${job_name}.sql"
    cat > "${sqlfile}" <<SQL
CREATE ROUTINE LOAD ${DB}.${job_name} ON ${table_name}
COLUMNS(${cols})
PROPERTIES (
    "desired_concurrent_number" = "1",
    "max_batch_interval" = "${MAX_INTERVAL}",
    "max_batch_rows" = "200000",
    "format" = "json",
    "strip_outer_array" = "false",
    "jsonpaths" = "${paths}"
)
FROM KAFKA (
    "kafka_broker_list" = "${BOOTSTRAP}",
    "kafka_topic" = "${topic}",
    "property.group.id" = "doris-${job_name}",
    "property.kafka_default_offsets" = "OFFSET_BEGINNING"
);
SQL

    log "创建 Routine Load：${job_name} -> ${DB}.${table_name} (topic=${topic})"
    if mysql -h "${FE_HOST}" -P "${QUERY_PORT}" -uroot --connect-timeout=15 \
            < "${sqlfile}" 2>&1 | head -5; then
        log "  已提交"
    fi
    rm -f "${sqlfile}"
}

# ------------------------------------------------------------
# topic 名与表名同名
# ------------------------------------------------------------
TABLES=(
    "dwd_trade_order_detail"
    "dwd_trade_payment_detail"
    "dwd_trade_refund_detail"
    "dwd_traffic_behavior_detail"
    "dws_traffic_overview_1m"
    "ads_realtime_trade_1m"
    "ads_realtime_traffic_1m"
    "ads_realtime_category_1m"
)

for t in "${TABLES[@]}"; do
    ensure_job "rl_${t}" "${t}" "${t}"
done

echo
log "当前 Routine Load 任务："
mysql_q -B -e "USE ${DB}; SHOW ROUTINE LOAD;" 2>/dev/null \
  | awk -F'\t' 'NR==1 {next} {printf "  %-36s State=%s\n", $2, $8}' || true

echo
log "完成"
