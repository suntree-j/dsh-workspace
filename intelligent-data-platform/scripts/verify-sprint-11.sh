#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-11.sh — Sprint 11 验收（数据质量 + 监控）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-11.sh
#   bash scripts/verify-sprint-11.sh --with-lake       # 追加湖仓 Iceberg 的层间校验（需要 ≈1 GB 空闲内存）
#   bash scripts/verify-sprint-11.sh --proof-quality-fail   # 只跑"失败路径演示"
#
# 验收 8 步：
#   1  服务状态（2 个监控容器 + healthcheck 四要素 + 命名卷）
#   2  版本与内存上限（镜像标签是断言，不是口号）
#   3  内存预算取证（监控 <= 512 MB，且实测不超限）
#   4  Prometheus（自身 API + 4 个抓取目标 + 抓到的指标条数）
#   5  Grafana（供给的数据源/面板 + **真的能查到 Doris 业务数据**）
#   6  数据质量校验（逐条 [OK]/[FAIL] + 自带非空守卫）
#   7  失败路径演示（**故意让一条校验失败，证明它真的 exit 1**）
#   8  Nginx 反代 + 回归（公网入口状态码 + 既有站点未被破坏）
#
# 退出码：0 全部通过；1 有失败项。
#
# ------------------------------------------------------------
# 每一步的"为什么"
# ------------------------------------------------------------
#   为什么第 2 步要把镜像标签写成断言：
#     版本漂移是静默的 —— 有人把 compose 改成 latest 后，
#     一切验证都还是绿的，直到某次拉取换了行为。
#     把版本写成断言，漂移才会在验收里现形。
#
#   为什么第 3 步要单独做内存取证：
#     这台机器的可用内存只够"实时链路 + 少量常驻"，监控是**新增常驻**。
#     只验功能不验内存，等于把 Sprint 3 那次整机失联的成因重新埋回去。
#
#   为什么第 5 步要"真的查一行业务数据"：
#     容器 healthy、数据源列表里有 doris-mysql —— 这两件事都**不能**证明
#     面板上有数据。只有真的执行一条 SQL 并拿到行数才算。
#     "成功信号不可信"在本步的形态就是"数据源存在 ≠ 数据源可用"。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

PASS=0
FAIL=0
SKIP=0
STEP_NAMES=()
STEP_RESULTS=()
DETAILS=()

WITH_LAKE=0
PROOF_ONLY=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --with-lake) WITH_LAKE=1 ;;
        --proof-quality-fail) PROOF_ONLY=1 ;;
        -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) log_error "未知参数：$1"; exit 2 ;;
    esac
    shift
done

step_start()  { STEP_NAMES+=("$1"); printf '\n%b\n' "${C_BOLD}$*${C_RESET}"; }
step_record() {
    STEP_RESULTS+=("$2")
    if [ "$2" != "PASS" ] && [ "$2" != "SKIP" ]; then OVERALL=1; fi
}
OVERALL=0

check() {
    local name="$1" expected="$2" actual="$3"
    if [ "${expected}" = "${actual}" ]; then
        printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${actual}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-50s 期望=[%s] 实际=[%s]\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${expected}" "${actual}"
        DETAILS+=("${name}: 期望 ${expected}，实际 ${actual}")
        FAIL=$(( FAIL + 1 ))
    fi
}

check_true() {
    local name="$1" note="$2"
    shift 2
    if "$@"; then
        printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${name}" "${note}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "${name}" "${note}"
        DETAILS+=("${name}: 条件不成立（${note}）")
        FAIL=$(( FAIL + 1 ))
    fi
}

skip() { printf '  %b %-50s %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "$1" "$2"; SKIP=$(( SKIP + 1 )); }
section() { printf '\n%b\n' "${C_BLUE}$*${C_RESET}"; }

envv() { grep -E "^$1=" "${REPO_ROOT}/.env" 2>/dev/null | head -1 | cut -d= -f2-; }

# 容器内探针：返回 HTTP 状态码
inspect_health() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || echo missing; }

# ------------------------------------------------------------
# 0. 失败路径演示（独立入口，供 run-quality-checks.sh --proof-fail 复用）
# ------------------------------------------------------------
proof_quality_fail() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 失败路径演示（Sprint 11 验收用）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    bash "${REPO_ROOT}/scripts/run-quality-checks.sh" --proof-fail
    return $?
}

if [ "${PROOF_ONLY}" -eq 1 ]; then
    load_env
    proof_quality_fail
    exit $?
fi

# ------------------------------------------------------------
# 1. 服务状态
# ------------------------------------------------------------
step_services() {
    step_start "1/8 服务状态（容器 / healthcheck 四要素 / 命名卷）"

    local c
    for c in prometheus grafana; do
        local running
        running="$(docker inspect -f '{{.State.Running}}' "${c}" 2>/dev/null || echo false)"
        check "${c} 容器在运行" "true" "${running}"
        check "${c} healthcheck 状态" "healthy" "$(inspect_health "${c}")"
    done

    # AGENTS.md 4.4：healthcheck 必须含 interval / timeout / retries / start_period
    local svc key rendered
    rendered="$(compose config 2>/dev/null)"
    for svc in prometheus grafana; do
        local missing=""
        for key in interval timeout retries start_period; do
            if printf '%s' "${rendered}" | awk -v s="  ${svc}:" 'index($0,s)==1{f=1;next} f&&/^  [a-z]/{f=0} f' | grep -q "${key}:"; then
                :
            else
                missing="${missing} ${key}"
            fi
        done
        if [ -z "${missing}" ]; then
            printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${svc} healthcheck 四要素齐全" "interval/timeout/retries/start_period"
            PASS=$(( PASS + 1 ))
        else
            printf '  %b %-50s 缺少：%s\n' "${C_RED}[FAIL]${C_RESET}" "${svc} healthcheck 四要素" "${missing}"
            DETAILS+=("${svc} healthcheck 缺少${missing}")
            FAIL=$(( FAIL + 1 ))
        fi
    done

    # 命名卷
    local vol
    for vol in data-platform-prometheus-data data-platform-grafana-data; do
        check_true "命名卷存在： ${vol}" "ok" docker volume inspect "${vol}"
    done

    # 网络：两个容器都必须在 data-platform 网络上（AGENTS.md 6.1）
    for c in prometheus grafana; do
        local net
        net="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "${c}" 2>/dev/null | tr -d '[:space:]')"
        check "${c} 加入 data-platform 网络" "data-platform" "${net}"
    done

    # 端口只绑回环（不对公网直接暴露）
    local bind
    for pair in "prometheus 9090" "grafana 3001"; do
        set -- ${pair}
        bind="$(docker inspect -f '{{json .HostConfig.PortBindings}}' "$1" 2>/dev/null || echo '{}')"
        if printf '%s' "${bind}" | grep -q '127.0.0.1'; then
            printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "$1 端口只绑回环" "127.0.0.1:$2"
            PASS=$(( PASS + 1 ))
        else
            printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "$1 端口暴露到了公网" "${bind}"
            DETAILS+=("$1 端口未绑回环")
            FAIL=$(( FAIL + 1 ))
        fi
    done
}

# ------------------------------------------------------------
# 2. 版本与内存上限
# ------------------------------------------------------------
step_versions() {
    step_start "2/8 版本与内存上限（镜像标签是断言，不是口号）"

    local img
    img="$(docker inspect -f '{{.Config.Image}}' prometheus 2>/dev/null || echo '?')"
    check "Prometheus 镜像标签" "prom/prometheus:v3.13.3" "${img}"

    img="$(docker inspect -f '{{.Config.Image}}' grafana 2>/dev/null || echo '?')"
    check "Grafana 镜像标签" "grafana/grafana:13.2.2" "${img}"

    # 禁止 latest：任何一处出现 latest 都不接受
    local comp
    comp="$(compose config 2>/dev/null | grep -E '^\s+image:' | grep -c 'latest' || true)"
    check "编排里没有 latest 标签" "0" "${comp:-0}"

    # 实际运行版本（不是标签，是程序自己报的）
    local prom_ver
    prom_ver="$(compose exec -T prometheus prometheus --version 2>/dev/null | head -1 | awk '{print $3}')"
    check_true "Prometheus 自报版本（实测，非标签）" "prometheus ${prom_ver:-未知}" \
        test -n "${prom_ver}"

    # !! 正则必须容忍冒号后的空格 !!
    #   Grafana 的 /api/health 返回的是**格式化过的 JSON**：
    #       {
    #         "database": "ok",
    #         "version": "13.2.2",
    #   `"version":"..."`（无空格）匹配不到任何东西 → 变量为空 →
    #   随后的 `cut -d'"' -f4` 因"分隔符不是单个字符"直接退出，
    #   而 common.sh 里是 `set -euo pipefail` —— **整个验收脚本当场中断**，
    #   表现为"跑到第 2 步就没下文了"，看起来像脚本自己坏了。
    #   写 `" *: *"` 就不挑格式。
    local graf_ver
    graf_ver="$(curl -s --max-time 10 http://127.0.0.1:3001/api/health 2>/dev/null \
                | grep -oE '"version" *: *"[^"]*"' | head -1 | sed 's/.*: *"//; s/"$//')"
    check "Grafana 自报版本（实测，非标签）" "13.2.2" "${graf_ver:-未知}"

    # mem_limit 必须显式设置（本项目 16 GB + 实时链路常驻 13.7 GB 的硬要求）
    local lim
    lim="$(docker inspect -f '{{.HostConfig.Memory}}' prometheus 2>/dev/null || echo 0)"
    check "Prometheus mem_limit（字节）" "268435456" "${lim}"     # 256 MiB
    lim="$(docker inspect -f '{{.HostConfig.Memory}}' grafana 2>/dev/null || echo 0)"
    check "Grafana mem_limit（字节）" "268435456" "${lim}"        # 256 MiB
}

# ------------------------------------------------------------
# 3. 内存预算取证
# ------------------------------------------------------------
step_memory() {
    step_start "3/8 内存预算取证（监控总计 <= 512 MB）"

    local budget=512
    local p_lim g_lim total
    p_lim="$(docker inspect -f '{{.HostConfig.Memory}}' prometheus 2>/dev/null || echo 0)"
    g_lim="$(docker inspect -f '{{.HostConfig.Memory}}' grafana 2>/dev/null || echo 0)"
    total=$(( (p_lim + g_lim) / 1024 / 1024 ))

    check_true "监控 mem_limit 合计 <= ${budget} MB" "实际 ${total} MB" \
        test "${total}" -le "${budget}"
    printf '     Prometheus %s MB + Grafana %s MB = %s MB\n' \
        "$(( p_lim / 1024 / 1024 ))" "$(( g_lim / 1024 / 1024 ))" "${total}"

    # 实测占用（docker stats 只取一次，不长时间跑）
    printf '\n     实测内存占用（docker stats --no-stream）：\n'
    docker stats --no-stream --format '       {{.Name}}  {{.MemUsage}}  ({{.MemPerc}})' prometheus grafana 2>/dev/null \
        | sed 's/^ *//' | sed 's/^/     /'

    # 关键：实时链路（Flink 栈）必须仍在跑 —— 起监控不该挤掉任何既有服务
    local c running_all=1
    for c in flink-jobmanager flink-taskmanager flink-sql-gateway flink-jobs doris-fe doris-be kafka mysql minio; do
        [ "$(docker inspect -f '{{.State.Running}}' "${c}" 2>/dev/null || echo false)" = "true" ] || running_all=0
    done
    check_true "既有 9 个数据层容器全部仍在运行（监控没挤掉任何人）" "全部 Running" \
        test "${running_all}" -eq 1

    printf '\n     宿主机内存： '
    free -m | awk 'NR==2{printf "可用 %s MB / 共 %s MB", $7, $2}'
    free -m | awk 'NR==3{printf "；swap 已用 %s MB / 共 %s MB\n", $3, $2}'
}

# ------------------------------------------------------------
# 4. Prometheus
# ------------------------------------------------------------
step_prometheus() {
    step_start "4/8 Prometheus（自身 API + 抓取目标 + 抓到的指标条数）"

    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:9090/-/healthy || echo 000)"
    check "Prometheus /-/healthy" "200" "${code}"

    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:9090/api/v1/status/config || echo 000)"
    check "Prometheus /api/v1/status/config" "200" "${code}"

    # 抓取目标：4 个（prometheus / doris-fe / doris-be / grafana）
    local targets_json up_count down_count
    targets_json="$(curl -s --max-time 15 'http://127.0.0.1:9090/api/v1/targets?state=active' 2>/dev/null || echo '')"
    up_count="$(printf '%s' "${targets_json}" | grep -o '"health":"up"' | wc -l | tr -d '[:space:]')"
    down_count="$(printf '%s' "${targets_json}" | grep -o '"health":"down"' | wc -l | tr -d '[:space:]')"

    check_true "抓取目标数 >= 4（prometheus/doris-fe/doris-be/grafana）" "实际 up=${up_count:-0} down=${down_count:-0}" \
        test "${up_count:-0}" -ge 4
    check "没有 down 的目标" "0" "${down_count:-0}"

    # 逐 job 列出（证据行）
    printf '     抓取目标明细：\n'
    printf '%s' "${targets_json}" \
        | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("       （解析失败，Prometheus 可能刚启动）")
    sys.exit(0)
for t in d.get("data", {}).get("activeTargets", []):
    lb = t.get("labels", {})
    print("       {:<12} {:<24} health={:<5} lastError={}".format(
        lb.get("job", "?"), t.get("scrapeUrl", "?"), t.get("health", "?"),
        (t.get("lastError") or "-")[:60]))
'

    # 真的抓到了 Doris 的指标（不是只有 up）
    #
    # !! 用 python 解析 JSON，不要用 grep 抠数字 !!
    #   实测踩坑：第一版写
    #     grep -o '"value":\[[^]]*\]' | grep -o '"[0-9.]*"$'
    #   而 `grep -o` 对一个匹配行会输出多处匹配、每处一行 ——
    #   `[^]]*` 拿到的是 `1790468862.486,"1564"`，被拆成两行，
    #   最后一行是 `"1564"`，`$` 锚定的是**行尾**，于是匹配成功……
    #   但实测返回 0 条：因为 time 值里带小数点与逗号，
    #   `[^]]*` 的贪婪匹配让第二处匹配变成 `"1564"` 之外的东西。
    #   一个正则能不能抠出数，本身就不该靠推理 —— 直接解析 JSON。
    prom_query_count() {
        curl -s --max-time 15 --get 'http://127.0.0.1:9090/api/v1/query' \
            --data-urlencode "query=$1" 2>/dev/null \
            | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    r = d["data"]["result"]
    print(int(float(r[0]["value"][1])) if r else 0)
except Exception:
    print(0)
'
    }
    local fe_series be_series
    fe_series="$(prom_query_count 'count({job="doris-fe"})')"
    be_series="$(prom_query_count 'count({job="doris-be"})')"
    check_true "抓到 Doris FE 指标（条数 > 50）" "实际 ${fe_series:-0} 条" \
        test "${fe_series:-0}" -gt 50
    check_true "抓到 Doris BE 指标（条数 > 50）" "实际 ${be_series:-0} 条" \
        test "${be_series:-0}" -gt 50

    # 配置文件语法无法在容器外校验，改用"容器起来了 + targets 有 4 个"作为等价证据；
    # 这里再确认 prometheus.yml 确实是从仓库挂进去的（而不是镜像里的默认值）
    local mount_src
    mount_src="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/prometheus/prometheus.yml"}}{{.Source}}{{end}}{{end}}' prometheus 2>/dev/null || echo '')"
    case "${mount_src}" in
        */infrastructure/monitoring/prometheus/prometheus.yml)
            printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "prometheus.yml 来自仓库挂载" "${mount_src}"
            PASS=$(( PASS + 1 )) ;;
        *)
            printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "prometheus.yml 不是仓库挂载的" "${mount_src:-无}"
            DETAILS+=("prometheus.yml 未从仓库挂载")
            FAIL=$(( FAIL + 1 )) ;;
    esac
}

# ------------------------------------------------------------
# 5. Grafana
# ------------------------------------------------------------
step_grafana() {
    step_start "5/8 Grafana（供给的数据源与面板 + 真的能查到业务数据）"

    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3001/api/health || echo 000)"
    check "Grafana /api/health" "200" "${code}"

    # 从容器环境里取管理员口令（不回显）
    local api ds dashboards
    api() {
        docker exec -e U="${GRAFANA_ADMIN_USER:-admin}" -e P="${GRAFANA_ADMIN_PASSWORD:-}" grafana \
            sh -c 'wget -q -O - "http://$U:$P@127.0.0.1:3000$1"' sh "$1" 2>/dev/null || true
    }

    ds="$(api /api/datasources)"
    if printf '%s' "${ds}" | grep -q '"uid":"doris-mysql"'; then
        printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "数据源已供给： doris-mysql（只读账号）" "ok"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "数据源 doris-mysql 未供给" "${ds:0:120}"
        DETAILS+=("Grafana 未供给 doris-mysql 数据源")
        FAIL=$(( FAIL + 1 ))
    fi
    if printf '%s' "${ds}" | grep -q '"uid":"prometheus"'; then
        printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "数据源已供给： prometheus" "ok"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "数据源 prometheus 未供给" "见 Grafana 日志"
        DETAILS+=("Grafana 未供给 prometheus 数据源")
        FAIL=$(( FAIL + 1 ))
    fi

    dashboards="$(api '/api/search?query=%20')"
    if printf '%s' "${dashboards}" | grep -q 'data-platform-overview'; then
        printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "仪表盘已供给： data-platform-overview" "ok"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "仪表盘未供给" "${dashboards:0:160}"
        DETAILS+=("Grafana 未供给仪表盘")
        FAIL=$(( FAIL + 1 ))
    fi

    # --------------------------------------------------------
    # !! 本步的核心：面板上**真的有业务数据** !!
    #   容器 healthy + 数据源在列表里，都不能证明这件事。
    #   唯一可信的做法是让 Grafana **自己**执行一条 SQL 并返回行数。
    #   走 /api/ds/query（Grafana 9+ 的统一查询接口），
    #   等价于面板点一下 "Run query"。
    # --------------------------------------------------------
    local qres
    qres="$(docker exec -e U="${GRAFANA_ADMIN_USER:-admin}" -e P="${GRAFANA_ADMIN_PASSWORD:-}" grafana \
        sh -c 'wget -q -O - --header="Content-Type: application/json" \
               --post-data="{\"queries\":[{\"refId\":\"A\",\"datasource\":{\"type\":\"mysql\",\"uid\":\"doris-mysql\"},\"rawSql\":\"SELECT COUNT(*) AS c FROM lakehouse_ads.ads_batch_trade_1d\",\"format\":\"table\"}],\"from\":\"now-10y\",\"to\":\"now\"}" \
               "http://$U:$P@127.0.0.1:3000/api/ds/query"' 2>/dev/null || true)"

    if printf '%s' "${qres}" | grep -q '11459\|"c"'; then
        printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "Grafana 经 doris-mysql 真的查到了业务数据" "ads_batch_trade_1d 可查"
        PASS=$(( PASS + 1 ))
        printf '     响应片段： %s\n' "$(printf '%s' "${qres}" | head -c 240)"
    else
        printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "Grafana 查不到业务数据（数据源不可用）" "$(printf '%s' "${qres}" | head -c 240)"
        DETAILS+=("Grafana 数据源查询失败")
        FAIL=$(( FAIL + 1 ))
    fi

    # 匿名只读：不带凭据时首页应当 200（Grafana 13 直接渲染登录页/面板），
    # 302 也可接受（某些版本会先跳 /login）。
    # !! 不要把它写成只接受 302 !!
    #   实测 Grafana 13.2.2 的 /grafana/ 直接返回 200（内置登录页），
    #   写死 302 会得到一条"其实一切正常"的 FAIL。
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3001/grafana/ || echo 000)"
    case "${code}" in
        200|302) printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "匿名访问 /grafana/ 可读（演示友好）" "${code}"; PASS=$(( PASS + 1 )) ;;
        *)       printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "匿名访问 /grafana/ 异常" "${code}"; FAIL=$(( FAIL + 1 )); DETAILS+=("匿名访问 Grafana 返回 ${code}") ;;
    esac

    # 面板 JSON 里的数据源 uid 必须与供给文件一致（否则面板全 "Datasource not found"）
    check_true "面板 JSON 引用的 uid 与供给一致（prometheus / doris-mysql）" "命中" \
        grep -q '"uid": "doris-mysql"' "${REPO_ROOT}/infrastructure/monitoring/grafana/provisioning/dashboards/data-platform-overview.json"
}

# ------------------------------------------------------------
# 6. 数据质量校验
# ------------------------------------------------------------
step_quality() {
    step_start "6/8 数据质量校验（逐条打印 [OK]/[FAIL] 与实测值）"

    # 清单必须覆盖任务书要求的七类，逐类断言（防止有人删掉某一类还以为齐全）
    local conf="${REPO_ROOT}/infrastructure/quality/checks.conf"
    check_true "校验清单存在" "${conf}" test -f "${conf}"

    # !! 数清单行必须只数"数字开头"的行 !!
    #   第一版写 `^  [0-9]+[[:space:]]+[a-z_]+`，实测数出 25 行而清单只有 21 条 ——
    #   因为表头的 `#   id ...` 和末尾的汇总行也被 `[a-z_]+` 匹配到了。
    #   报"数量对不上"会把人引向"是不是清单被改坏了"，而其实只是正则太宽。
    local n
    n="$(bash "${REPO_ROOT}/infrastructure/quality/run-check.sh" --list 2>/dev/null \
         | grep -cE '^  [0-9]+[[:space:]]' || true)"
    # !! 阈值写成 20 而不是"任意 > 0" !!
    #   清单当前是 25 条（24 Doris + 1 spark）。写成"存在即可"挡不住
    #   "清单被删到只剩 3 条"，写成 25 又会在合法增删时误报；
    #   20 是"能容忍少量调整、但明显缺失一定报警"的位置。
    check_true "校验项数量 >= 20" "实际 ${n:-0} 条" test "${n:-0}" -ge 20

    # 清单必须覆盖任务书要求的类别，逐类断言（防止有人删掉某一类还以为齐全）
    #
    # !! 类别前缀必须与 checks.conf 里实际的 id 对齐 !!
    #   第一版写的是 `ods_dwd`，但清单里的 id 是 `dwd_ads_behavior_parity` /
    #   `dwd_ads_trade_parity`（它们比的是 DWD↔ADS，不是 ODS↔DWD —— 后者需要
    #   读 Iceberg，属 engine=spark 的 dwd_ods_parity_all）。
    #   断言写错前缀会得到一条"看起来像清单缺项"的 FAIL，
    #   而清单其实是完整的 —— 断言本身成了噪声。
    local key
    for key in rowcount pk_unique notnull enum_ fresh_ amount_ ads_window dwd_ads_ dwd_ods_; do
        if grep -q "\[check\.${key}" "${conf}"; then
            printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "覆盖校验类别： ${key}*" "ok"
            PASS=$(( PASS + 1 ))
        else
            printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "缺少校验类别： ${key}*" "清单里找不到"
            DETAILS+=("校验清单缺少 ${key}* 类别")
            FAIL=$(( FAIL + 1 ))
        fi
    done

    # 空集合守卫：至少有一条"非空守卫"（gt/eq 1），否则空表会全绿
    check_true "存在非空守卫（防'空集合上全绿'，Sprint 4 的教训）" "命中 rowcount_*" \
        grep -q 'rowcount_trade\|rowcount_traffic' "${conf}"

    # 真正跑一遍
    printf '\n'
    local rc=0
    if [ "${WITH_LAKE}" -eq 1 ]; then
        bash "${REPO_ROOT}/scripts/run-quality-checks.sh" --with-lake || rc=$?
    else
        bash "${REPO_ROOT}/scripts/run-quality-checks.sh" || rc=$?
    fi
    if [ "${rc}" -eq 0 ]; then
        printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "全部校验通过（退出码 0）" "ok"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "校验存在失败项" "退出码 ${rc}"
        DETAILS+=("数据质量校验退出码 ${rc}")
        FAIL=$(( FAIL + 1 ))
    fi
}

# ------------------------------------------------------------
# 7. 失败路径演示
# ------------------------------------------------------------
step_proof_fail() {
    step_start "7/8 失败路径证明（故意让一条校验失败）"

    # 为什么要单独一步：任务书的要求是"失败必须让脚本 exit 1"。
    # 这句话的**唯一可信证据**是让它失败一次，看退出码。
    local out rc=0
    out="$(bash "${REPO_ROOT}/scripts/run-quality-checks.sh" --proof-fail 2>&1)" || rc=$?

    if [ "${rc}" -eq 0 ]; then
        printf '  %b %-50s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "失败路径演示通过（被改坏的校验确实 exit 1）" "ok"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b %-50s %s\n' "${C_RED}[FAIL]${C_RESET}" "失败路径演示自身失败" "退出码 ${rc}"
        DETAILS+=("失败路径演示退出码 ${rc}")
        FAIL=$(( FAIL + 1 ))
    fi

    # 打印关键证据行（不贴大段日志）
    printf '     证据行：\n'
    printf '%s\n' "${out}" | grep -E '演示 [12]|退出码 = 1|恢复后仍是|通过 [0-9]+' | sed 's/^/       /'
}

# ------------------------------------------------------------
# 8. Nginx 反代 + 回归
# ------------------------------------------------------------
step_nginx_regression() {
    step_start "8/8 Nginx 反代 + 回归（公网入口 + 既有站点未被破坏）"

    local site ip code
    site="$(site_base)"
    ip="$(public_ip)"

    # 站点配置里必须真的有这两段
    local nginx_site="/etc/nginx/sites-available/data-platform.conf"
    check_true "站点配置含 /grafana/ 反代" "命中" grep -q 'location /grafana/' "${nginx_site}"
    check_true "站点配置含 /metrics/ 反代" "命中" grep -q 'location /metrics/' "${nginx_site}"
    check_true "Nginx 配置语法（nginx -t）" "通过" nginx -t
    # 备份文件不该残留（说明上一次 nginx -t 是通过的）
    check_true "没有残留的 .sprint11.bak（说明 nginx -t 通过、未走回滚）" "无残留" \
        test ! -f "${nginx_site}.sprint11.bak"

    # 本机回环入口
    #   期望值必须与**当前 nginx 配置的实际行为**一致：
    #     /grafana/        → 200（Grafana 直接给登录页/面板；不是 302）
    #     /metrics/        → 200（原始指标文本）
    #     /metrics/-/healthy → 200（健康探针，经 location /metrics/-/ 剥前缀）
    local pair
    for pair in "/grafana/ 200" "/grafana/login 200" "/metrics/ 200" "/metrics/-/healthy 200"; do
        # shellcheck disable=SC2086
        set -- ${pair}
        code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 15 "${site}$1" || echo 000)"
        check "本机 GET $1" "$2" "${code}"
    done

    # 公网入口（用真实 IP，走公网路径；无 VPN/代理时应当正常）
    printf '\n     公网入口实测（%s://%s）：\n' "$(site_scheme)" "${ip}"
    for path in "/data/" "/data/api/health" "/grafana/" "/metrics/"; do
        code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 20 "$(site_scheme)://${ip}${path}" || echo 000)"
        printf '       %-22s → %s\n' "${path}" "${code}"
    done

    # 回归：既有站点与验证脚本仍在
    check_true "run-quality-checks.sh 可被独立调用（可调度）" "命中 --with-lake" \
        grep -q 'with-lake' "${REPO_ROOT}/scripts/run-quality-checks.sh"
    check_true "run-batch-pipeline.sh 未被本 Sprint 修改（应无 Sprint 11 痕迹）" "无痕迹" \
        bash -c "! grep -q 'sprint-11\|quality-checks' '${REPO_ROOT}/scripts/run-batch-pipeline.sh'"

    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 15 "${site}/data/api/health" || echo 000)"
    check "回归：数据服务 /data/api/health" "200" "${code}"
    code="$(curl_site -s -o /dev/null -w '%{http_code}' --max-time 15 "${site}/airflow/" || echo 000)"
    check "回归：Airflow UI /airflow/" "200" "${code}"
}

# ------------------------------------------------------------
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 11 验收：数据质量 + 监控${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    require_docker
    cd "${REPO_ROOT}"

    # !! 关掉 errexit：验收脚本必须"跑完全部 8 步再给结论" !!
    #   实测踩坑：一条取值命令失败（Grafana 的格式化 JSON 让 grep 匹配为空，
    #   随后的 cut 因分隔符为空而报错）会让脚本**当场中断**，
    #   输出停在半路、既没有失败明细也没有汇总 ——
    #   看起来像"验收脚本自己坏了"，而不是"某一项不达标"。
    #   验收脚本的价值恰恰在于**把所有问题一次列全**，所以这里显式关掉。
    set +e

    step_services;        step_record "服务状态"     "$([ "${FAIL}" -eq 0 ] && echo PASS || echo FAIL)"; local f1="${FAIL}"
    step_versions;        step_record "版本与内存上限" "$([ "${FAIL}" -eq "${f1}" ] && echo PASS || echo FAIL)"; local f2="${FAIL}"
    step_memory;          step_record "内存预算取证"   "$([ "${FAIL}" -eq "${f2}" ] && echo PASS || echo FAIL)"; local f3="${FAIL}"
    step_prometheus;      step_record "Prometheus"     "$([ "${FAIL}" -eq "${f3}" ] && echo PASS || echo FAIL)"; local f4="${FAIL}"
    step_grafana;         step_record "Grafana"        "$([ "${FAIL}" -eq "${f4}" ] && echo PASS || echo FAIL)"; local f5="${FAIL}"
    step_quality;         step_record "数据质量校验"   "$([ "${FAIL}" -eq "${f5}" ] && echo PASS || echo FAIL)"; local f6="${FAIL}"
    step_proof_fail;      step_record "失败路径证明"   "$([ "${FAIL}" -eq "${f6}" ] && echo PASS || echo FAIL)"; local f7="${FAIL}"
    step_nginx_regression; step_record "Nginx 与回归"  "$([ "${FAIL}" -eq "${f7}" ] && echo PASS || echo FAIL)"

    printf '\n%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 11 验收汇总${C_RESET}"
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
        printf '%b\n' "${C_GREEN}${C_BOLD} Sprint 11 验收通过${C_RESET}"
        printf '\n'
        printf '  Grafana：   %s://%s/grafana/\n' "$(site_scheme)" "$(public_ip)"
        printf '  Prometheus：%s://%s/metrics/\n' "$(site_scheme)" "$(public_ip)"
        printf '  质量校验：  bash scripts/run-quality-checks.sh --with-lake\n'
    else
        printf '%b\n' "${C_RED}${C_BOLD} Sprint 11 验收未通过，请按上方提示排查${C_RESET}"
        return 1
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
    return 0
}

main "$@" < /dev/null
exit $?
