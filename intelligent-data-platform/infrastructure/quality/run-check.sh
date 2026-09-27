#!/usr/bin/env bash
# ============================================================
# infrastructure/quality/run-check.sh — 单条数据质量校验的执行引擎
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash infrastructure/quality/run-check.sh --list
#   bash infrastructure/quality/run-check.sh --id amount_gmv_realtime
#   bash infrastructure/quality/run-check.sh --id amount_gmv_realtime --expect-override 0.01
#   bash infrastructure/quality/run-check.sh --all            # 逐条执行，打印 [OK]/[FAIL]
#
# 退出码：
#   0  校验通过
#   1  校验失败（**实测值与期望值不符**）
#   2  用法错误
#   3  校验项不存在或配置不完整
#
# ------------------------------------------------------------
# 为什么需要"期望值可覆盖"（--expect-override）
# ------------------------------------------------------------
#   "失败必须真的失败"这句话需要一个**可复现的证明**：
#   把某条校验的期望值换成不可能出现的值，它必须立刻报 [FAIL] 并 exit 1。
#   没有这个开关，就只能靠"理论上它会失败"来自证，那不是证据。
#   见 scripts/verify-sprint-11.sh 的"失败路径演示"一步。
#
# ------------------------------------------------------------
# 为什么要区分 error / warn
# ------------------------------------------------------------
#   有些校验项依赖环境（例如湖仓需要 1 GB 空闲内存才能读），
#   它们不该把整个流水线判红。severity=warn 的项失败时只报告，
#   不改变退出码 —— 但一定会打印出来，不会静默吞掉。
# ============================================================

set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${HERE}/../.." && pwd)"
CHECKS_CONF="${CHECKS_CONF:-${HERE}/checks.conf}"

# .env 必须被加载：docker compose 需要它做变量替换，
# 而且下面的 doris_scalar 走的是 compose exec（凭据不落在命令行里）。
# shellcheck source=../../scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"

# !! load_env 刻意**不在这里**调用 !!
#   common.sh 的 load_env 在 .env 缺失时会 `exit 1`（那是它的既定行为，
#   对 start/stop/health-check 这类脚本是对的）。
#   但本引擎有两个**完全不需要凭据**的模式：`--list` 与 `--dry-run`
#   （只解析清单、打印 SQL，不连数据库）。
#   实测踩坑：`.env` 一旦缺失（本机真发生过，见 SPRINT_11.md §10.2），
#   `--list` 也一起挂掉并打印"找不到 .env"，于是连"清单里有多少条校验"
#   都查不出来 —— **把可用性问题伪装成了配置缺失**。
#   因此改为在真正要连数据库的路径上按需加载（见下面的 ensure_env）。

# 新鲜度阈值（小时）。理由见 checks.conf 第七节的说明。
#   48h 而不是 26h：本机日批的稳态滞后实测 31.8h，26h 会把正确的校验写成恒红。
FRESH_WINDOW_HOURS="${FRESHNESS_WINDOW_HOURS:-48}"
# dt 取当天 00:00，判据里减 6 分钟摊掉分钟级取整（等价于 <= 48h）
FRESH_PARTITION_HOURS="${FRESHNESS_PARTITION_HOURS:-47.9}"

MODE=""
CHECK_ID=""
EXPECT_OVERRIDE=""
DRY_RUN=0

# ------------------------------------------------------------
# 颜色（仅在 TTY 下启用，和 scripts/lib/common.sh 保持一致）
# ------------------------------------------------------------
if [ -t 1 ]; then
    C_RED='\033[0;31m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'
    C_BLUE='\033[0;34m'; C_BOLD='\033[1m'; C_RESET='\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''; C_RESET=''
fi

usage() {
    cat <<'EOF'
用法：
  bash infrastructure/quality/run-check.sh --list
  bash infrastructure/quality/run-check.sh --id <校验项 id> [--expect-override <值>]
  bash infrastructure/quality/run-check.sh --all [--dry-run]

说明：
  --all 会逐条打印 [OK]/[FAIL] 与**实测值**；有 error 级失败时退出码为 1。
  engine=spark 的校验项在 --all 下会被跳过（需要读湖仓，约 1 GB 内存），
  由 scripts/run-quality-checks.sh --with-lake 统一驱动。
  --dry-run 只解析清单、不连数据库（用于评审与 CI 语法检查）。

  --expect-override 把期望值换成别的值，用来**证明失败路径真的会失败**
  （见 scripts/verify-sprint-11.sh 的失败路径演示）。
EOF
}

die() { printf '%b\n' "${C_RED}[FAIL]${C_RESET} $*" >&2; exit "${2:-3}"; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --list)            MODE="list" ;;
        --all)             MODE="all" ;;
        --id)              MODE="one"; CHECK_ID="${2:-}"; shift ;;
        --expect-override) EXPECT_OVERRIDE="${2:-}"; shift ;;
        --dry-run)         DRY_RUN=1 ;;
        -h|--help)         usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

[ -n "${MODE}" ] || { usage >&2; exit 2; }
[ -f "${CHECKS_CONF}" ] || die "找不到校验清单：${CHECKS_CONF}" 3

# ------------------------------------------------------------
# 把 checks.conf 读成一个 TSV：id / severity / engine / 字段...
#
# 为什么用 python3 做解析而不是纯 bash 正则：
#   配置里有 `key value` 形式的字段（值里含中文与冒号），
#   用 bash 切词会在"值里带空格"的项上出错；python 读起来直白且可测。
#   本机脚本一直用 python3（Airflow / 数据服务都在宿主 venv 上）。
# ------------------------------------------------------------
parse_conf() {
    python3 - "$CHECKS_CONF" <<'PY'
import re
import sys

path = sys.argv[1]
checks = []
cur = None
id_re = re.compile(r'^\[check\.([A-Za-z0-9_]+)\]\s*$')
kv_re = re.compile(r'^([a-z_]+)\s+(.*)$')

with open(path, encoding='utf-8') as fh:
    for raw in fh:
        line = raw.rstrip('\n')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        m = id_re.match(line)
        if m:
            cur = {'id': m.group(1)}
            checks.append(cur)
            continue
        if cur is None:
            continue
        m = kv_re.match(line)
        if m:
            cur[m.group(1)] = m.group(2).strip()

# 输出制表符分隔；字段顺序固定，便于 bash 读
fields = ('id', 'title', 'domain', 'severity', 'engine', 'sql',
          'op', 'expect', 'fresh_field', 'description', 'hint')
for c in checks:
    print('\t'.join(c.get(k, '') for k in fields))
PY
}

# ------------------------------------------------------------
# 判定比较
#
# 为什么用 awk 而不是 `[ ]`：
#   实测值可能是空、可能是小数（26h 的滞后是 24.94）、可能是 DECIMAL 字符串。
#   bash 的 `-lt` 只吃整数，空值会直接报语法错并（在 set -e 下）中断整个脚本。
#   awk 对空值/非数值一律当 0 处理，且支持浮点。
# ------------------------------------------------------------
compare() {
    local actual="$1" op="$2" expected="$3"
    if [ "${op}" = "eq" ]; then
        # 字符串等值：先做数值等价比较（"1" 与 "1.0" 必须算相等），
        # 数值不可比时退化为字面比较（用于 expect 是日期的场景）。
        if awk -v a="${actual}" -v b="${expected}" 'BEGIN{ if (a+0==b+0 && length(a)>0 && length(b)>0) exit 0; exit 1 }'; then
            return 0
        fi
        [ "${actual}" = "${expected}" ]
        return $?
    fi

    if [ -z "${expected}" ]; then
        return 3
    fi

    awk -v a="${actual}" -v b="${expected}" -v o="${op}" 'BEGIN{
        if (length(a) == 0) a = 0
        if (o == "gt") exit !(a >  b)
        if (o == "ge") exit !(a >= b)
        if (o == "lt") exit !(a <  b)
        if (o == "le") exit !(a <= b)
        if (o == "eq") exit !(a == b)
        exit 2
    }'
    return $?
}

op_symbol() {
    case "$1" in
        gt) printf '>';; ge) printf '>=';; lt) printf '<';; le) printf '<=';; eq) printf '==';; *) printf '%s' "$1";;
    esac
}

# ------------------------------------------------------------
# 把 SQL 文件压成**一行**（mysql -e 需要单行，且避免 heredoc 干扰）
#
# !! 为什么必须逐行剔除整行注释，而不能直接 `tr -d '\n'` !!
#   实测踩坑（本 Sprint 最隐蔽的一个）：
#     SQL 文件开头是若干行 `-- 说明` 注释。`tr -d '\n'` 把换行删掉之后，
#     第一行注释与后面的 SELECT **粘在同一行**：
#       -- 交易域非空守卫：... -- 返回 1 = 全部非空；... SELECT (...)
#     SQL 的单行注释一直持续到行尾 —— 于是**整条语句都成了注释**，
#     mysql 不报错、退出码 0、没有任何输出。
#     引擎看到空结果就报"查询失败/无返回"，把 24 条校验全部判红，
#     而真因是"注释吃掉了语句"。**这类"成功执行但什么都没做"的故障，
#     只有回到目标状态（这里：把同一个 SQL 单独跑一遍）才能发现。**
#
#   做法：逐行读，丢弃空白行与以 `--` 开头的整行注释，其余行用空格连接；
#   `--` 后面的分号会让 mysql 把它当多语句分隔符，因此顺带删掉行尾分号。
# ------------------------------------------------------------
sql_oneline() {
    local file="$1"
    awk '
        { sub(/\r$/, "") }
        /^[[:space:]]*$/ { next }
        /^[[:space:]]*--/ { next }
        {
            gsub(/;[[:space:]]*$/, "")
            printf "%s ", $0
        }
    ' "${file}"
}

# ------------------------------------------------------------
# 只在"真的要连数据库"时加载 .env
#   --list / --dry-run 走不到这里，因此 .env 缺失时它们仍然可用。
# ------------------------------------------------------------
ENV_LOADED=0
ensure_env() {
    if [ "${ENV_LOADED}" -eq 0 ]; then
        load_env
        ENV_LOADED=1
    fi
}

# ------------------------------------------------------------
# 查询执行
# ------------------------------------------------------------
doris_scalar() {
    local sql_file="$1" sql_text out
    sql_text="$(sql_oneline "${sql_file}")"
    if [ "${DRY_RUN}" -eq 1 ]; then
        printf '(dry-run) %s' "${sql_text}"
        return 0
    fi
    ensure_env
    out="$(
        compose exec -T doris-be mysql -h 172.28.0.10 -P 9030 -uroot -N \
            --connect-timeout=5 -e "${sql_text}" 2>&1 < /dev/null
    )" || {
        printf '%s\n' "${out}" >&2
        return 1
    }
    # Doris 对 DECIMAL 会补小数位；只取最后一个非空行（末尾可能有警告）
    printf '%s\n' "${out}" | grep -vE '^[[:space:]]*$' | tail -n 1 | tr -d '[:space:]'
}

spark_scalar() {
    local sql_file="$1" out
    if [ "${DRY_RUN}" -eq 1 ]; then
        printf '(dry-run spark) %s' "$(sql_oneline "${sql_file}")"
        return 0
    fi
    ensure_env
    out="$(bash "${REPO_ROOT}/scripts/spark-sql.sh" -e "$(sql_oneline "${sql_file}")" 2>/dev/null)" || return 1
    printf '%s\n' "${out}" \
        | grep -vE 'WARN|Spark Web UI|Spark master|To adjust|Time taken|^$|^[a-z_]+$' \
        | tail -n 1 | tr -d '[:space:]'
}

print_evidence() {
    local file="${HERE}/checks/_evidence_counts.sql"
    [ -f "${file}" ] || return 0
    local txt
    txt="$(doris_scalar "${file}" 2>/dev/null || true)"
    [ -n "${txt}" ] && printf '  实测快照（orders/payments/refunds/behaviors/ads_trade_1m/ads_traffic_1m/gmv_realtime/gmv_batch）：\n    %s\n' "${txt}"
    return 0
}

# ------------------------------------------------------------
# 执行一条校验
#   参数：TSV 行（可选 --expect-override 生效）
#   返回：0 通过 / 1 失败 / 4 跳过（engine=spark 且未允许）
# ------------------------------------------------------------
run_one() {
    local row="$1"
    local id title domain severity engine sql_rel op expect fresh_field description hint
    IFS=$'\t' read -r id title domain severity engine sql_rel op expect fresh_field description hint <<< "${row}"

    [ -n "${id}" ] || return 1
    [ -n "${sql_rel}" ] || die "校验项 ${id} 缺少 sql 字段" 3
    local sql_file="${HERE}/${sql_rel}"
    [ -f "${sql_file}" ] || die "校验项 ${id} 的 SQL 不存在：${sql_file}" 3

    local effective_expect="${expect}"
    if [ -n "${EXPECT_OVERRIDE}" ]; then
        effective_expect="${EXPECT_OVERRIDE}"
    fi
    effective_expect="${effective_expect//\{FRESH_WINDOW_HOURS\}/${FRESH_WINDOW_HOURS}}"
    effective_expect="${effective_expect//\{FRESH_PARTITION_HOURS\}/${FRESH_PARTITION_HOURS}}"

    local actual rc=0
    case "${engine}" in
        doris) actual="$(doris_scalar "${sql_file}")" || rc=1 ;;
        spark) actual="$(spark_scalar "${sql_file}")" || rc=1 ;;
        *)     die "校验项 ${id} 的 engine 非法：${engine}" 3 ;;
    esac

    if [ "${rc}" -ne 0 ] || [ -z "${actual}" ]; then
        printf '  %b %-52s 查询失败/无返回\n' "${C_RED}[FAIL]${C_RESET}" "${title}"
        [ -n "${hint}" ] && printf '         提示：%s\n' "${hint}"
        return 1
    fi

    if compare "${actual}" "${op}" "${effective_expect}"; then
        local extra=""
        if [ -n "${fresh_field}" ]; then
            extra="${fresh_field#*|}"
        fi
        if [ -n "${extra}" ]; then
            printf '  %b %-52s 实测 %s（%s） %s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${title}" \
                "${actual}" "${extra}" "$(op_symbol "${op}")" "${effective_expect}"
        else
            printf '  %b %-52s 实测 %s %s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${title}" \
                "${actual}" "$(op_symbol "${op}")" "${effective_expect}"
        fi
        return 0
    fi

    printf '  %b %-52s 实测 %s，期望 %s %s\n' "${C_RED}[FAIL]${C_RESET}" "${title}" \
        "${actual}" "$(op_symbol "${op}")" "${effective_expect}"
    [ -n "${description}" ] && printf '         这条在防：%s\n' "${description}"
    [ -n "${hint}" ] && printf '         提示：%s\n' "${hint}"
    return 1
}

# ------------------------------------------------------------
# --list：只打印清单（不连数据库，便于 CI / 评审）
# ------------------------------------------------------------
do_list() {
    printf '%b\n' "${C_BOLD}数据质量校验清单（${CHECKS_CONF}）${C_RESET}"
    printf '  %-3s %-30s %-9s %-8s %-6s %s\n' '#' 'id' 'domain' 'severity' 'engine' '规则'
    local n=0 row id title domain severity engine sql_rel op expect rest
    while IFS= read -r row; do
        n=$(( n + 1 ))
        IFS=$'\t' read -r id title domain severity engine sql_rel op expect rest <<< "${row}"
        printf '  %-3s %-30s %-9s %-8s %-6s %s %s\n' "${n}" "${id}" "${domain}" "${severity}" \
            "${engine}" "${op}" "${expect}"
    done < <(parse_conf)
    printf '\n  合计 %s 条（含 4 条新鲜度 + 层间/金额/枚举/主键/非空/行数）\n' "${n}"
}

# ------------------------------------------------------------
# --all：逐条执行
# ------------------------------------------------------------
do_all() {
    local row id title domain severity engine sql_rel op expect rest
    local pass=0 fail=0 skip=0 warn_fail=0
    local pending_spark=()

    while IFS= read -r row; do
        IFS=$'\t' read -r id title domain severity engine sql_rel op expect rest <<< "${row}"

        # spark 类校验由 run-quality-checks.sh 在内存闸门后单独驱动
        # （判活内存必须独立成步骤，见 verify-sprint-11.sh 的内存一步）
        if [ "${engine}" = "spark" ]; then
            pending_spark+=("${id}")
            skip=$(( skip + 1 ))
            printf '  %b %-52s engine=spark，交由 --with-lake 驱动\n' "${C_YELLOW}[SKIP]${C_RESET}" "${title}"
            continue
        fi

        if run_one "${row}"; then
            pass=$(( pass + 1 ))
        else
            if [ "${severity}" = "warn" ]; then
                warn_fail=$(( warn_fail + 1 ))
                printf '         （severity=warn：只报告，不影响退出码）\n'
            else
                fail=$(( fail + 1 ))
            fi
        fi
    done < <(parse_conf)

    if [ "${#pending_spark[@]}" -gt 0 ]; then
        printf '\n  待湖仓校验（%s 条）：%s\n' "${#pending_spark[@]}" "${pending_spark[*]}"
        printf '    驱动方式： bash scripts/run-quality-checks.sh --with-lake\n'
    fi

    printf '\n  小结：通过 %s   失败 %s   跳过 %s   （warn 级失败 %s）\n' \
        "${pass}" "${fail}" "${skip}" "${warn_fail}"

    [ "${fail}" -eq 0 ]
}

# ------------------------------------------------------------
# main
# ------------------------------------------------------------
case "${MODE}" in
    list) do_list ;;
    all)  do_all ;;
    one)
        [ -n "${CHECK_ID}" ] || { usage >&2; exit 2; }
        row="$(parse_conf | awk -F'\t' -v id="${CHECK_ID}" '$1 == id {print; found=1} END{exit !found}')" \
            || die "校验清单里没有 id=${CHECK_ID}" 3
        printf '%b\n' "${C_BOLD}执行单条校验：${CHECK_ID}${C_RESET}"
        if [ "${DRY_RUN}" -eq 1 ]; then
            local_row="${row}"
            IFS=$'\t' read -r _r_id _r_title _r_dom _r_sev _r_eng _r_sql _r_op _r_exp _r_f _r_d _r_h <<< "${local_row}"
            printf '  engine    : %s\n' "${_r_eng}"
            printf '  规则      : %s %s\n' "${_r_op}" "${_r_exp}"
            printf '  SQL       : %s\n' "${_r_sql}"
            printf '  SQL 正文  : %s\n' "$(tr -d '\n' < "${HERE}/${_r_sql}")"
            exit 0
        fi
        if run_one "${row}"; then
            exit 0
        fi
        printf '\n'
        print_evidence
        exit 1
        ;;
esac
