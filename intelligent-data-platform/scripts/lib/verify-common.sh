#!/usr/bin/env bash
# ============================================================
# scripts/lib/verify-common.sh — 验收脚本共用的断言工具（Sprint 8 起）
# ============================================================
#
# 被 verify-sprint-8.sh / verify-sprint-9.sh 引用，不由用户直接执行。
#
# !! 为什么把这一套单独抽出来 !!
#   verify-sprint-7.sh 里已经有一份完整的断言工具（check / check_contains /
#   check_any / check_in_source / skip / summary 相关的计数）。Sprint 8 与 9
#   都要写验收脚本，若各自再抄一份，就会出现三份会独立漂移的实现 ——
#   而**断言本身写错比没有断言更危险**（它会把正确的产品代码报成缺陷，
#   见 SPRINT_7.md 第 9.4~9.6 节）。
#
#   verify-sprint-7.sh **保持原样不动**：它已验收通过（49/49），
#   为了"消除重复"去动一个已通过的验收脚本，收益小于风险。
#   从 Sprint 8 起的新脚本统一 source 本文件。
#
# 使用方式（在脚本里）：
#   source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/verify-common.sh"
#   check "某项" "期望" "实际"
#   check_contains "某项" "关键字" "${json_body}"
#   summary && exit 0
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"

# 计数（各验收脚本共享同一组，避免每个脚本自己再定义一遍）
PASS=0
FAIL=0
SKIP=0
DETAILS=()
VERIFY_TITLE="${VERIFY_TITLE:-验收}"

# ------------------------------------------------------------
# 断言：精确相等
# ------------------------------------------------------------
check() {
    local name="$1" expected="$2" actual="$3"
    if [ "${expected}" = "${actual}" ]; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${actual}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s %s  (期望 %s)\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${actual}" "${expected}"
        DETAILS+=("${name}: 实际 ${actual} / 期望 ${expected}")
        FAIL=$(( FAIL + 1 ))
    fi
}

# ------------------------------------------------------------
# 内部：在关闭 pipefail 的前提下做"包含"判断
#
# !! 为什么必须临时关掉 pipefail（Sprint 7 实测踩坑，见 SPRINT_7.md 9.4）!!
#   `printf '%s' "$大字符串" | grep -qF -- '关键字'` 明明命中，放进 if 里却是假，
#   退出码实测 141。原因：grep -q 命中后立即退出并关闭管道读端，
#   printf 还没写完就收到 SIGPIPE 而死（141）；pipefail 下管道取"最后一个失败者"，
#   于是返回 141 而不是 grep 的 0。
#   只在这两个函数内部关闭，脚本其余部分保持 pipefail 开启。
# ------------------------------------------------------------
_contains() {
    set +o pipefail
    local rc=0
    printf '%s' "$2" | grep -qF -- "$1" || rc=$?
    set -o pipefail
    return "${rc}"
}

# ------------------------------------------------------------
# JSON 响应归一化
#
# !! 为什么必须归一化 !!
#   FastAPI 默认输出**紧凑 JSON**：{"code":"NOT_SELECT","message":"..."}
#   而手写或 python -m json.tool 是：{"code": "NOT_SELECT", ...}
#   断言写死带空格的版本 → 真实响应永远匹配不上，
#   表现为"拒绝原因不正确"，而功能其实完全正常。
#   注意：**只用于 JSON 响应**，不要用在源码文本上（见 check_in_source）。
# ------------------------------------------------------------
normalize_json() {
    printf '%s' "$1" | sed -E 's/:[[:space:]]+/:/g; s/,[[:space:]]+/,/g'
}

check_contains() {
    local name="$1" needle="$2" haystack="$3"
    if _contains "${needle}" "$(normalize_json "${haystack}")"; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "命中 ${needle}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "未命中 ${needle}"
        DETAILS+=("${name}: 输出中未找到 ${needle}")
        FAIL=$(( FAIL + 1 ))
    fi
}

# 命中任一即算通过（同一语义可能有多种写法）
check_any() {
    local name="$1" haystack="$2"; shift 2
    local needle norm
    norm="$(normalize_json "${haystack}")"
    for needle in "$@"; do
        if _contains "${needle}" "${norm}"; then
            printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "命中 ${needle}"
            PASS=$(( PASS + 1 ))
            return 0
        fi
    done
    printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "未命中任何一种写法：$*"
    DETAILS+=("${name}: 输出中未找到 $*")
    FAIL=$(( FAIL + 1 ))
}

# 断言**不**包含（用于负例：同义词不该乱命中、输出不该泄漏凭据）
check_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    if _contains "${needle}" "$(normalize_json "${haystack}")"; then
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "不应出现 ${needle}"
        DETAILS+=("${name}: 输出中出现了不应有的 ${needle}")
        FAIL=$(( FAIL + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "未出现 ${needle}"
        PASS=$(( PASS + 1 ))
    fi
}

# ------------------------------------------------------------
# 在**源码文本**里查找片段（命中任一即通过）
#
# !! 为什么不能复用 check_any !!
#   check_any 会先做 JSON 空白归一化，源码里的 `key: 'ask'`
#   会被改成 `key:'ask'` 而再也匹配不上。
# ------------------------------------------------------------
check_in_source() {
    local name="$1" haystack="$2"; shift 2
    local needle
    for needle in "$@"; do
        if _contains "${needle}" "${haystack}"; then
            printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "命中 ${needle}"
            PASS=$(( PASS + 1 ))
            return 0
        fi
    done
    printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "未命中任何一种写法：$*"
    DETAILS+=("${name}: 源码中未找到 $*")
    FAIL=$(( FAIL + 1 ))
}

# ------------------------------------------------------------
# 正例断言：**非空守卫**
#
# !! 为什么需要它 !!
#   "在空集合上全绿通过"是本项目踩过的坑（Sprint 4 的自检曾在空表上空洞通过）。
#   凡是断言"每个元素都满足某条件"的地方，都必须先证明**确实有元素**。
# ------------------------------------------------------------
check_nonempty() {
    local name="$1" count="$2"
    if [ "${count}" -gt 0 ] 2>/dev/null; then
        printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${count} 条（非空）"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-52s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "为空（不能在空集合上判定通过）"
        DETAILS+=("${name}: 集合为空，判定无意义")
        FAIL=$(( FAIL + 1 ))
    fi
}

skip() {
    printf '  %b %-52s %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "$1" "$2"
    SKIP=$(( SKIP + 1 ))
}

section() { printf '\n%b\n' "${C_BOLD}$*${C_RESET}"; }

# ------------------------------------------------------------
# JSON 取值（用 python3 解析，避免在 bash 里正则抠 JSON）
#
# 为什么不在 bash 里抠：嵌套 JSON 用 sed/grep 抠必然在某个边界上出错，
# 而出错的方式是"取到空字符串"，接着断言就会以误导性的方式失败。
# python3 在服务器上必然存在（数据服务与 Agent 都跑在它上面）。
#
# !! 为什么 JSON 走 stdin 而不是命令行参数 !!
#   `/ask` 的响应实测可达数十 KB（含 steps / plan / docs），
#   而 Linux 的**单个参数**长度上限是 MAX_ARG_STRLEN = 128 KB。
#   一旦超过，`execve` 直接返回 E2BIG（"Argument list too long"）——
#   表现为断言全部拿到空值，看起来像"接口没返回数据"，
#   而实际上接口好得很。走 stdin 没有这个上限。
# ------------------------------------------------------------
_json_py() {
    local py=""
    if command -v python3 >/dev/null 2>&1; then
        py="python3"
    fi
    if [ -z "${py}" ]; then
        return 1
    fi
    "${py}" -c "$@"
}

# json_get <json> [点分路径]
json_get() {
    printf '%s' "$1" | _json_py '
import json, sys
raw = sys.stdin.read()
path = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    data = json.loads(raw)
except Exception:
    print("")
    sys.exit(0)
for part in path.split("."):
    if not part:
        continue
    if isinstance(data, list):
        try:
            data = data[int(part)]
        except Exception:
            print(""); sys.exit(0)
    elif isinstance(data, dict):
        data = data.get(part)
    else:
        print(""); sys.exit(0)
    if data is None:
        print(""); sys.exit(0)
if isinstance(data, (dict, list)):
    print(json.dumps(data, ensure_ascii=False))
else:
    print(data)
' "${2:-}"
}

# json_len <json> [点分路径] —— 取长度（不存在时输出 0）
json_len() {
    printf '%s' "$1" | _json_py '
import json, sys
raw = sys.stdin.read()
path = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    data = json.loads(raw)
except Exception:
    print(0); sys.exit(0)
for part in path.split("."):
    if not part:
        continue
    if isinstance(data, list):
        try:
            data = data[int(part)]
        except Exception:
            print(0); sys.exit(0)
    elif isinstance(data, dict):
        data = data.get(part)
    else:
        print(0); sys.exit(0)
print(len(data) if isinstance(data, (list, dict, str)) else 0)
' "${2:-}"
}

# json_has <json> [点分路径] —— 路径存在且非空即 0
json_has() {
    local value
    value="$(json_get "$1" "${2:-}")"
    [ -n "${value}" ]
}

# json_is_true <json> [点分路径] —— 值必须是布尔真（避免"非空即真"的误判）
json_is_true() {
    local value
    value="$(json_get "$1" "${2:-}")"
    case "${value}" in
        True|true|1) return 0 ;;
        *)           return 1 ;;
    esac
}

# ------------------------------------------------------------
# HTTP 工具
# ------------------------------------------------------------
http_code() { curl_site -s -o /dev/null -w '%{http_code}' --max-time "${HTTP_TIMEOUT:-30}" "$@"; }

# get_json <url>  —— 回环访问本机服务，避开公网抖动（链路问题不是应用问题）
get_json() { curl -s --max-time "${HTTP_TIMEOUT:-30}" "$@"; }

post_json() {
    local url="$1" body="$2"; shift 2
    curl -s --max-time "${HTTP_TIMEOUT:-60}" -X POST "${url}" \
        -H 'Content-Type: application/json' -d "${body}" "$@"
}

# ------------------------------------------------------------
# 汇总
# ------------------------------------------------------------
summary() {
    printf '\n%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} ${VERIFY_TITLE} 验收汇总${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '  通过 %s   失败 %s   跳过 %s\n' "${PASS}" "${FAIL}" "${SKIP}"
    if [ "${SKIP}" -gt 0 ]; then
        printf '  %b 跳过项不计入通过：请确认它们不是被掩盖的失败\n' "${C_YELLOW}[注意]${C_RESET}"
    fi
    if [ "${FAIL}" -gt 0 ]; then
        printf '\n%b\n' "${C_RED}失败明细：${C_RESET}"
        local d
        for d in "${DETAILS[@]}"; do
            printf '  - %s\n' "${d}"
        done
        printf '\n'
        log_error "${VERIFY_TITLE} 验收未通过"
        return 1
    fi
    printf '\n'
    log_ok "${VERIFY_TITLE} 验收通过：通过 ${PASS} / 失败 0 / 跳过 ${SKIP}"
    return 0
}
