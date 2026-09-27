#!/usr/bin/env bash
# ============================================================
# scripts/verify-sprint-10.sh — Sprint 10 验收（MCP：只读数据能力经 MCP 暴露）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/verify-sprint-10.sh
#
# 验收项（6 步）：
#   1. 服务就绪     MCP 依赖已装；systemd 单元已装并 active；/healthz 报告能力面
#   2. 能力面可枚举  MCP 声明的工具 == 4 个只读工具；与 Agent 的取数工具**逐名相等**
#   3. 最小权限实证 MCP 进程无凭据（单元不读 .env、源码无驱动/口令、无写工具）
#   4. 只读边界     经 MCP 写操作必须被 sqlguard 拒绝（不是"没实现"，是"被拒绝"）
#   5. 端到端一致性 **同一问题经 MCP 与经直接 HTTP 得到一致结果**（逐字段比对）
#   6. 回归         Sprint 7 / 8 / 9 不许回退 + 新增 MCP 单元测试
#
# !! 为什么第 5 步是本次验收的核心 !!
#   Sprint 10 的主张是"把**同一份**只读能力用 MCP 再暴露一次"。
#   因此唯一有意义的判据是：**同一个 SQL，两条路径给出同一个结果**。
#   只证明"MCP 能返回数据"是不够的 —— 那可能是一条完全独立（且口径漂移）的实现。
#   这里逐字段比对 rows 的 JSON 规范化结果，差异数为 0 才算通过。
#
# !! 第 3 步为什么用"扫源码"这种笨办法 !!
#   "这个进程没有数据库凭据"在运行期无法自证（它当然可以说自己没读）。
#   唯一可核对的是"代码与单元文件里没有这条路径"：不读 .env、不 import 驱动、
#   不出现口令键名。这类断言必须落在**文本**上。
#
# 退出码：0 = 通过（SKIP 不算失败）；非 0 = 有验收项失败
# ============================================================

# shellcheck source=lib/verify-common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/verify-common.sh"

VERIFY_TITLE="Sprint 10"

# !! 变量名刻意**不叫** MCP_HOST / MCP_PORT !!
#   那两个名字是 MCP 服务自己的环境变量（systemd 单元里注入），
#   而本脚本会 `load_env`（把 .env 里的键 export 出去）。
#   一旦日后有人把 MCP_HOST 写进 .env，`load_env` 就会覆盖本脚本的同名变量，
#   于是验收脚本会去探测一个**别的地址**，并给出与事实无关的结论。
#   加 `MCP_SERVER_` 前缀把"验收脚本用的地址"与"服务自己的配置"分开。
MCP_SERVER_HOST="${MCP_SERVER_HOST:-127.0.0.1}"
MCP_SERVER_PORT="${MCP_SERVER_PORT:-8200}"
MCP_URL="http://${MCP_SERVER_HOST}:${MCP_SERVER_PORT}/mcp"
MCP_HEALTH="http://${MCP_SERVER_HOST}:${MCP_SERVER_PORT}/healthz"
AGENT_PORT="${AGENT_PORT:-8100}"
AGENT="http://127.0.0.1:${AGENT_PORT}"
API="${API_BASE:-http://127.0.0.1:8000}"

MCP_DIR="${REPO_ROOT}/services/mcp"
MCP_UNIT_SRC="${MCP_DIR}/deploy/data-platform-mcp.service"
MCP_UNIT_DST="/etc/systemd/system/data-platform-mcp.service"
MCP_USER="dpmcp"
PROBE="${MCP_DIR}/tools/mcp_probe.py"

# ------------------------------------------------------------
# 前置检查：部署前提不满足就**停手**，不往下做
#
# !! 为什么必须显式检查 `.env` !!
#   本脚本的 `deploy_mcp` 会**改单元文件、重启服务**。
#   而 `/opt/data-platform/.env` 是所有服务与 `docker compose` 的唯一配置来源：
#   它缺失时 `docker compose` 连插值都做不了（65 个变量无值），
#   此时再去重启任何服务只会把"已停"变成"更乱"。
#   一个会改机器状态的验收脚本，必须先在门口确认机器处于可用状态 ——
#   否则它会把一个**配置缺失**放大成一次**服务中断**。
# ------------------------------------------------------------
preflight() {
    section "前置检查（配置与编排可用性）"

    local env_file="${REPO_ROOT}/.env"
    if [ ! -f "${env_file}" ]; then
        printf '  %b %s 不存在\n' "${C_RED}[FAIL]${C_RESET}" "${env_file}"
        printf '\n  这是一个**部署前提未满足**的状态，不是本 Sprint 的缺陷：\n'
        printf '  所有服务与 docker compose 都依赖它。请先恢复配置，再跑本验收。\n'
        printf '  （本脚本不会继续执行任何会改机器状态的动作。）\n\n'
        exit 1
    fi
    printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" ".env 存在" \
        "$(stat -c '%A %U:%G' "${env_file}" 2>/dev/null || echo '?')"
    PASS=$(( PASS + 1 ))

    if [ -f "${REPO_ROOT}/docker-compose.yml" ]; then
        if (cd "${REPO_ROOT}" && docker compose config --quiet >/dev/null 2>&1); then
            printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "docker compose config 通过" "ok"
            PASS=$(( PASS + 1 ))
        else
            printf '  %b %-52s %s\n' "${C_YELLOW}[WARN]${C_RESET}" "docker compose config 未通过" \
                "本 Sprint 不依赖 compose，继续"
            SKIP=$(( SKIP + 1 ))
        fi
    fi
}

# 验收用 SQL：固定、只读、结果小且**必然非空**（空集合上的一致性没有意义）。
# 为什么选实时 ADS 表：它是实时链路常驻写入的表，任何时刻都有窗口数据；
# 而离线 ADS 表可能因尚未跑批而为空 —— 那样第 5 步会在"两边都是空"上通过。
VERIFY_SQL="SELECT window_start, gmv, order_cnt FROM ecommerce.ads_realtime_trade_1m ORDER BY window_start DESC LIMIT 5"

# 与 MCP 服务应当暴露的工具集（顺序无关，逐个断言）
EXPECTED_TOOLS=(metrics_lookup tables_lookup sql_query reconciliation)

# ------------------------------------------------------------
# 选解释器：优先 Agent venv（mcp SDK 装在它里面）
# ------------------------------------------------------------
pick_python() {
    if [ -x "${REPO_ROOT}/.venv-agent/bin/python" ]; then
        printf '%s' "${REPO_ROOT}/.venv-agent/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        command -v python3
    else
        printf ''
    fi
}

# probe <子命令> [参数...] —— 调 MCP 探针并返回 JSON
probe() {
    local py="$1"; shift
    "${py}" "${PROBE}" "$@" --url "${MCP_URL}" 2>/dev/null || true
}

# urlencode <文本> —— 拼 POST 请求体时用（避免在 bash 里手写 JSON 转义）
urlencode() {
    _json_py 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$1"
}

# compare_rows <左侧 JSON> <右侧 JSON> —— 逐字段比对（键排序，消除键顺序差异）
# 输出：IDENTICAL / DIFFERENT(n vs m) / PARSE_ERROR
compare_rows() {
    _json_py '
import json, sys
def norm(text):
    try:
        return json.loads(text)
    except Exception:
        return None
left, right = norm(sys.argv[1]), norm(sys.argv[2])
if left is None or right is None:
    print("PARSE_ERROR")
else:
    print("IDENTICAL" if left == right else "DIFFERENT(%d vs %d)" % (len(left), len(right)))
' "$1" "$2"
}

# ============================================================
# 1. 服务就绪
# ============================================================
step_services() {
    section "1/6 服务就绪（依赖、单元、健康探针）"

    local py
    py="$(pick_python)"
    if [ -z "${py}" ]; then
        skip "MCP 依赖核对" "找不到可用的 python"
        skip "MCP 服务" "找不到可用的 python"
        return
    fi

    local version
    version="$("${py}" -c 'import importlib.metadata as m; print(m.version("mcp"))' 2>/dev/null || true)"
    if [ -n "${version}" ]; then
        check "mcp SDK 版本与 requirements.txt 钉的版本一致" \
            "$(grep -E '^mcp==' "${MCP_DIR}/requirements.txt" | head -1 | cut -d= -f3)" "${version}"
    else
        check "已安装 mcp SDK（import + 版本可查）" "yes" "no"
    fi

    if ! command -v systemctl >/dev/null 2>&1; then
        skip "MCP systemd 单元" "本机没有 systemctl（非服务器环境？）"
    else
        check "MCP 单元文件已安装（${MCP_UNIT_DST}）" "yes" \
            "$([ -f "${MCP_UNIT_DST}" ] && echo yes || echo no)"
        check "MCP 单元处于 active" "active" \
            "$(systemctl is-active data-platform-mcp 2>/dev/null || true)"
        check "MCP 单元 enabled（重启机器后自动起）" "enabled" \
            "$(systemctl is-enabled data-platform-mcp 2>/dev/null || true)"
    fi

    local health
    health="$(get_json "${MCP_HEALTH}")"
    check "GET ${MCP_HEALTH} 状态码" "200" "$(http_code "${MCP_HEALTH}")"
    check_contains "健康探针报告只读能力" '"read_only":true' "${health}"
    check_contains "健康探针声明**不持有**数据库凭据" '"database_credentials":false' "${health}"
    check_contains "健康探针报出数据出口（只读数据服务）" '"data_api_base"' "${health}"

    # 两条传输都要在自述里可见（stdio 是本地宿主形态，不能被悄悄去掉）
    local src
    src="$(cat "${MCP_UNIT_SRC}")"
    check_in_source "单元用 streamable-http 部署" "${src}" '--transport streamable-http'
    check_in_source "stdio 形态仍可用（--transport 可选）" \
        "$(cat "${MCP_DIR}/app/server.py")" 'choices=("streamable-http", "stdio")'
}

# ============================================================
# 2. 能力面可枚举 + 最小化
# ============================================================
step_capability_surface() {
    section "2/6 能力面（最小必要能力，可枚举）"

    local py listed
    py="$(pick_python)"
    if [ -z "${py}" ]; then
        skip "MCP 工具清单" "找不到可用的 python"
        return
    fi

    listed="$(probe "${py}" list)"
    check_contains "MCP list_tools 成功" '"ok":true' "${listed}"

    local count
    count="$(json_len "${listed}" "tools")"
    check_nonempty "MCP 声明的工具数（非空守卫）" "${count:-0}"
    check "MCP 工具数恰为 4（最小必要集合）" "4" "${count}"

    local tool
    for tool in "${EXPECTED_TOOLS[@]}"; do
        check_contains "MCP 暴露了只读工具 ${tool}" "\"${tool}\"" "${listed}"
    done

    # 不许有写工具/通用出口
    check_not_contains "MCP 没有 DDL/DML 工具" '"execute_ddl"' "${listed}"
    check_not_contains "MCP 没有任意 HTTP 出口" '"http_request"' "${listed}"
    check_not_contains "MCP 没有文件读写工具" '"read_file"' "${listed}"

    # Agent 侧：两条路径的能力面必须对得上
    local agent_mcp
    agent_mcp="$(get_json "${AGENT}/mcp")"
    check "GET /mcp 状态码" "200" "$(http_code "${AGENT}/mcp")"
    check_contains "Agent 报告取数路径配置" '"path"' "${agent_mcp}"
    check_contains "Agent 报告 MCP 端点" "${MCP_SERVER_PORT}/mcp" "${agent_mcp}"
    # 判据用**机器可读的布尔字段**，不用中文散文。
    # 第一版断言的是 `不会自动回退` 这句话 —— 一旦措辞微调（例如改成"**不会自动回退**"
    # 之外的写法），断言就会因为**文字变了**而失败，而它所表达的行为毫无变化。
    # 用 `no_silent_fallback: true` 之后，这条断言锁的是**语义**，不是文案。
    check_contains "Agent 明确声明不自动回退（机器可读字段）" '"no_silent_fallback":true' "${agent_mcp}"

    # Agent 交给模型的工具声明里，取数类工具必须与 MCP 的工具名一一对应
    local agent_tools
    agent_tools="$(get_json "${AGENT}/tools")"
    for tool in "${EXPECTED_TOOLS[@]}"; do
        check_contains "Agent 工具面含 ${tool}" "\"name\":\"${tool}\"" "${agent_tools}"
    done
}

# ============================================================
# 3. 最小权限实证（无凭据）
# ============================================================
step_no_credentials() {
    section "3/6 最小权限实证（MCP 进程里没有数据库凭据）"

    local unit
    unit="$(cat "${MCP_UNIT_SRC}")"

    # !! 判据必须按"行首的 systemd 指令"匹配，不能整篇找子串 !!
    #   第一版写的是 `check_not_contains "单元不读 .env" "EnvironmentFile=" "${unit}"`，
    #   结果是**假失败**：单元文件里有一句注释
    #   `# ⚠️ 刻意**没有** EnvironmentFile=/opt/data-platform/.env`
    #   —— 那句注释恰恰是在解释"我们为什么不读它"，却把断言弄红了。
    #   这类假失败最危险的地方在于它**很容易被顺手放宽**
    #   （改成"允许出现一次"之类），那就把真正的防线一起丢掉了。
    #   正确做法：只认真正的指令行（行首、可带缩进的 `EnvironmentFile=`）。
    local efile_lines
    efile_lines="$(printf '%s\n' "${unit}" | grep -cE '^[[:space:]]*EnvironmentFile[[:space:]]*=' || true)"
    check "单元**不读** .env（无 EnvironmentFile 指令行）" "0" "${efile_lines}"
    check_contains "单元只注入非敏感变量" 'MCP_DATA_API_BASE=http://127.0.0.1:8000' "${unit}"
    check_contains "单元只监听回环" 'MCP_HOST=127.0.0.1' "${unit}"
    check_not_contains "单元不以 root 运行" 'User=root' "${unit}"

    # 运行期复核：MCP 进程的环境里不许出现数据层口令
    local pid environ
    pid="$(pgrep -f 'app.main --transport streamable-htt[p]' | head -1 || true)"
    if [ -z "${pid}" ]; then
        skip "MCP 进程环境扫描" "没找到 MCP 进程（服务未以该命令行运行？）"
    else
        environ="$(tr '\0' '\n' < "/proc/${pid}/environ" 2>/dev/null || true)"
        check_nonempty "读到 MCP 进程的环境变量（非空守卫）" "$(printf '%s' "${environ}" | grep -c . || true)"
        check_not_contains "进程环境里没有 DORIS_ROOT_PASSWORD" "DORIS_ROOT_PASSWORD" "${environ}"
        check_not_contains "进程环境里没有 MYSQL_ROOT_PASSWORD" "MYSQL_ROOT_PASSWORD" "${environ}"
        check_not_contains "进程环境里没有 MINIO_ROOT_PASSWORD" "MINIO_ROOT_PASSWORD" "${environ}"
        check_not_contains "进程环境里没有 API_DORIS_PASSWORD" "API_DORIS_PASSWORD" "${environ}"
        check_not_contains "进程环境里没有 LLM_API_KEY" "LLM_API_KEY" "${environ}"
    fi

    # 源码复核：没有数据库驱动、没有口令键名、没有任意 HTTP 出口
    local all_src
    all_src="$(cat "${MCP_DIR}/app/server.py" "${MCP_DIR}/app/client.py" "${MCP_DIR}/app/config.py")"
    check_not_contains "MCP 源码不含 mysql 驱动" "import pymysql" "${all_src}"
    check_not_contains "MCP 源码不含 mysql-connector" "mysql.connector" "${all_src}"
    check_not_contains "MCP 源码不含数据层口令键名" "DORIS_ROOT_PASSWORD" "${all_src}"
    check_not_contains "MCP 源码不含 S3A 密钥" "s3a.secret.key" "${all_src}"

    # 唯一的取数通道必须仍是 POST /query
    check_contains "MCP 的唯一取数通道是 POST /query" '"POST"' "${all_src}"
    check_contains "MCP 取数走 /query 路径" '"/query"' "${all_src}"
}

# ============================================================
# 4. 只读边界（写入必须被拒绝）
# ============================================================
step_readonly_boundary() {
    section "4/6 只读边界（经 MCP 写操作被 sqlguard 拒绝）"

    local py
    py="$(pick_python)"
    if [ -z "${py}" ]; then
        skip "经 MCP 的写操作" "找不到可用的 python"
        return
    fi

    # 4.1 正常只读查询必须成功且非空（后续一致性断言的前提）
    local read
    read="$(probe "${py}" read "${VERIFY_SQL}")"
    check_contains "经 MCP 的合法查询成功" '"ok":true' "${read}"
    local rows
    rows="$(json_len "${read}" "rows")"
    check_nonempty "经 MCP 查到行数（非空守卫）" "${rows:-0}"
    check_contains "MCP 返回了实际执行的 SQL（可核对）" '"executed_sql"' "${read}"
    check_contains "服务端强制加了 LIMIT" "LIMIT 5" "${read}"

    # 4.2 四类攻击必须被拒绝，且**拒绝原因正确**
    #     与 verify-sprint-7.sh 用同样的四类：不 SELECT / 未授权表 / 多语句 / 注释
    #
    # ## !! 这里必须先"定性"，再决定改判据还是改实现 !!（实测记录）
    #
    # 第一版断言的是 MCP 协议层的 `is_error: true`，实测为 **false**，
    # 于是报"写操作未被拒"。**在改断言之前必须先回答：拒绝到底发生了没有？**
    # 实测原始返回（`DELETE FROM ecommerce.dwd_trade_order_detail`）：
    #
    #   MCP 侧：is_error = False（协议层未置位）
    #           structured_content = {"ok": false,
    #                                 "error": {"code": "NOT_SELECT",
    #                                           "message": "只允许 SELECT 查询",
    #                                           "detail": "实际语句以 'DELETE' 开头",
    #                                           "http_status": 400}}
    #   HTTP 侧：**HTTP 400** + {"error":{"code":"NOT_SELECT",...}}
    #
    # 定性：**拒绝真实发生了**，而且原因来自 `sqlguard`（证据是 HTTP 400 与同一个 code）。
    # 所以这**不是**安全缺陷，修的是**断言**。
    #
    # ## 两层错误形状的差异（刻意的设计，不是不一致）
    #
    #   HTTP 层用 **HTTP 状态码**表达失败（4xx）——这是 REST 的语义；
    #   MCP  层刻意**不用** `is_error`：`is_error` 表示"工具调用本身出错了"，
    #   而"SQL 被守卫拒绝"是**有价值的业务反馈**，模型看到 code 就能改写 SQL
    #   （AGENTS §10.1 第 6 条「查询失败必须能够重新规划」）。
    #   若把它塞进 is_error，模型拿到的是一个"工具炸了"的信号，
    #   而不是"你查了未授权的表"这种可操作的反馈。
    #   只有**链路级**故障（连不上数据服务）才走 MCP 的异常路径。
    #
    # ## 因此判据是三条同时成立（比"存在即通过"硬得多）
    #
    #   ① 被拒：`"ok":false`
    #   ② 拒绝码正确：出现具体的 code（NOT_SELECT / TABLE_NOT_ALLOWED / …）
    #   ③ **没有返回任何行**：不得出现 `"rows":[{` 与 `"executed_sql"` ——
    #      这一条才是"没被吞掉"的证据；少了它，"被拒"可能只是返回值长相不对
    local write
    _assert_rejected() {
        # _assert_rejected <名称> <期望拒绝码> <探针输出>
        local name="$1" code="$2" body="$3"
        check_contains "${name}（被拒：ok:false）" '"ok":false' "${body}"
        check_contains "${name}（拒绝码 ${code}）" "${code}" "${body}"
        check_not_contains "${name}（未返回任何行）" '"executed_sql"' "${body}"
    }

    write="$(probe "${py}" write "DELETE FROM ecommerce.dwd_trade_order_detail")"
    _assert_rejected "写操作被拒（DELETE）" "NOT_SELECT" "${write}"

    write="$(probe "${py}" write "SELECT order_id FROM ecommerce.dwd_user_profile LIMIT 1")"
    _assert_rejected "未授权表被拒" "TABLE_NOT_ALLOWED" "${write}"

    write="$(probe "${py}" write "SELECT 1 FROM ecommerce.ads_realtime_trade_1m; SELECT 2")"
    _assert_rejected "多语句被拒" "MULTI_STATEMENT" "${write}"

    write="$(probe "${py}" write "SELECT gmv FROM ecommerce.ads_realtime_trade_1m -- 注入")"
    _assert_rejected "注释注入被拒" "COMMENT" "${write}"

    # ③ 的独立取证：**底层数据服务自己也拒**（证明拒绝来自守卫，不是 MCP 自己拦的）
    local http_reject
    http_reject="$(curl -s -o /tmp/reject.json -w '%{http_code}' --max-time 20 \
        -X POST "${API}/query" -H 'Content-Type: application/json' \
        -d '{"sql": "DELETE FROM ecommerce.dwd_trade_order_detail", "limit": 200}' || true)"
    check "底层数据服务对 DELETE 返回 400（拒绝来自 sqlguard）" "400" "${http_reject}"
    check_contains "底层数据服务的拒绝码同为 NOT_SELECT" "NOT_SELECT" "$(cat /tmp/reject.json 2>/dev/null)"

    # 4.3 数据服务侧权限**没有**被 MCP 放大：MCP 走的就是同一个只读账号
    local health
    health="$(get_json "${API}/health")"
    check_contains "数据服务仍用只读账号" '"readonly_enforced":true' "${health}"
}

# ============================================================
# 5. 端到端一致性：同一问题，MCP 与直接 HTTP 必须一致
# ============================================================
step_e2e_consistency() {
    section "5/6 端到端一致性（同一问题经 MCP 与经直接 HTTP）"

    local py
    py="$(pick_python)"
    if [ -z "${py}" ]; then
        skip "MCP ↔ HTTP 一致性" "找不到可用的 python"
        return
    fi

    # 5.1 经 MCP
    local via_mcp
    via_mcp="$(probe "${py}" read "${VERIFY_SQL}")"
    check_contains "MCP 路径返回成功" '"ok":true' "${via_mcp}"

    # 5.2 经直接 HTTP（与 Agent 直连路径打的是同一个接口）
    #     VERIFY_SQL 里只有普通空格与字母数字（无引号/反斜杠），可以直接内插进 JSON 字符串。
    local body
    body="$(post_json "${API}/query" "{\"sql\": \"${VERIFY_SQL}\", \"limit\": 200}")"
    check_contains "HTTP 路径返回成功" '"row_count"' "${body}"

    # 5.3 先证明两条路径都**真的取到了数据**，再比一致性。
    #     "两边都为空"是完全一致，但毫无意义 —— 这正是 Sprint 4 踩过的
    #     "在空表上空洞通过"。所以非空守卫必须放在比对之前。
    local mcp_rows http_rows
    mcp_rows="$(json_len "${via_mcp}" "rows")"
    http_rows="$(json_len "${body}" "data.rows")"
    check_nonempty "MCP 路径行数（非空守卫）" "${mcp_rows:-0}"
    check_nonempty "HTTP 路径行数（非空守卫）" "${http_rows:-0}"
    check "两条路径行数相同" "${http_rows}" "${mcp_rows}"

    # 5.4 逐字段比对（JSON 规范化：键排序，消除键顺序差异带来的假失败）
    local mcp_norm http_norm diff
    mcp_norm="$(json_get "${via_mcp}" "rows")"
    http_norm="$(json_get "${body}" "data.rows")"
    diff="$(compare_rows "${mcp_norm}" "${http_norm}")"
    check "两侧 rows 逐字段一致（JSON 规范化后）" "IDENTICAL" "${diff}"

    # 5.5 关键字段单独取证（验收报告要能贴出两边的值）
    #
    # !! 展示与比较必须用**同一套归一化** !!
    #   上一版直接 `check` 两侧的原始 JSON 文本，于是"键顺序不同"被报成失败 ——
    #   而紧接着的一致性断言（键排序后比较）明明是 IDENTICAL。
    #   同一屏里两条断言互相矛盾，读的人只会怀疑产品。
    #   现在两边都过 `normalize_json`（只归一化空白）**并且**用 python 按键排序输出，
    #   显示的形态与比较的形态一致。
    local mcp_first http_first
    mcp_first="$(json_get "${via_mcp}" "rows.0")"
    http_first="$(json_get "${body}" "data.rows.0")"
    local mcp_pretty http_pretty
    mcp_pretty="$(_json_py 'import json,sys; print(json.dumps(json.loads(sys.argv[1]), ensure_ascii=False, sort_keys=True))' "${mcp_first}" 2>/dev/null || echo "${mcp_first}")"
    http_pretty="$(_json_py 'import json,sys; print(json.dumps(json.loads(sys.argv[1]), ensure_ascii=False, sort_keys=True))' "${http_first}" 2>/dev/null || echo "${http_first}")"
    printf '  %b MCP  path 第 1 行：%s\n' "${C_BOLD}" "${mcp_pretty}"
    printf '  %b HTTP path 第 1 行：%s\n' "${C_BOLD}" "${http_pretty}"
    check "第 1 行完全一致（键排序后逐字节）" "${http_pretty}" "${mcp_pretty}"
    check "两侧 row_count 一致" "$(json_get "${body}" "data.row_count")" "$(json_get "${via_mcp}" "row_count")"

    # 5.6 executed_sql 也必须一致（守卫改写后的语句是同一句）
    check "两侧 executed_sql 一致" \
        "$(json_get "${body}" "data.executed_sql")" "$(json_get "${via_mcp}" "executed_sql")"
}

# ============================================================
# 6. 回归
# ============================================================
step_regression() {
    section "6/6 回归（Sprint 7 / 8 / 9 不许回退 + 新增 MCP 单测）"

    local py
    py="$(pick_python)"
    if [ -z "${py}" ]; then
        skip "pytest 回归" "找不到可用的 python"
        return
    fi
    if ! "${py}" -c 'import pytest' >/dev/null 2>&1; then
        skip "pytest 回归" "${py} 里没有 pytest"
        return
    fi

    local out target
    for target in tests/test_mcp.py tests/test_agent.py tests/test_agent_graph.py tests/test_retrieval.py tests/test_sql_guard.py; do
        if out="$(cd "${REPO_ROOT}" && "${py}" -m pytest "${target}" -q 2>&1)"; then
            printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${target}" "$(printf '%s\n' "${out}" | tail -n 1)"
            PASS=$(( PASS + 1 ))
        else
            printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "${target}"
            printf '%s\n' "${out}" | tail -n 25
            DETAILS+=("${target} 失败")
            FAIL=$(( FAIL + 1 ))
        fi
    done

    # 新增的 MCP 测试必须是纯单元测试（不依赖外部服务，AGENTS.md §8.2）
    check_in_source "MCP 测试已声明为单元测试" "$(cat "${REPO_ROOT}/tests/test_mcp.py")" 'pytest.mark.unit'

    # Sprint 7 的验收脚本不许回退（Agent 工具层被改过：新增了取数路径分流）
    #
    # 判据用 `-f`（文件存在且可读）而不是 `-x`（可执行位）：
    # 我们是 `bash <脚本>` 调它，**不需要**可执行位。用 `-x` 会在
    # "文件在但没加可执行位"时静默跳过整条回归 —— 而 skip 不计入失败，
    # 于是一个**真实的回归检查**会以"已跳过"的形式消失（本脚本第一版就踩了这条）。
    if [ -f "${REPO_ROOT}/scripts/verify-sprint-7.sh" ]; then
        local s7
        if s7="$(cd "${REPO_ROOT}" && bash scripts/verify-sprint-7.sh 2>&1)"; then
            printf '  %b %-52s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "verify-sprint-7.sh 仍通过" \
                "$(printf '%s\n' "${s7}" | grep -E '通过 [0-9]+' | tail -1)"
            PASS=$(( PASS + 1 ))
        else
            printf '  %b %-52s\n' "${C_RED}[FAIL]${C_RESET}" "verify-sprint-7.sh 回归失败"
            printf '%s\n' "${s7}" | tail -n 20
            DETAILS+=("verify-sprint-7.sh 回退")
            FAIL=$(( FAIL + 1 ))
        fi
    else
        skip "verify-sprint-7.sh 回归" "脚本不存在（${REPO_ROOT}/scripts/verify-sprint-7.sh）"
    fi
}

# ============================================================
# 部署 MCP 服务（幂等）
# ============================================================
deploy_mcp() {
    section "0/6 部署 MCP 服务（幂等）"

    if ! command -v systemctl >/dev/null 2>&1; then
        skip "MCP 服务部署" "本机没有 systemctl"
        return
    fi

    # 专用系统用户（与 Agent 的 dpagent 同一手法：非 root、无特权组）
    if ! id "${MCP_USER}" >/dev/null 2>&1; then
        if useradd --system --no-create-home --shell /usr/sbin/nologin "${MCP_USER}" 2>/dev/null; then
            printf '  %b 已创建系统用户 %s\n' "${C_GREEN}[ OK ]${C_RESET}" "${MCP_USER}"
        else
            printf '  %b 创建系统用户 %s 失败\n' "${C_RED}[FAIL]${C_RESET}" "${MCP_USER}"
            DETAILS+=("无法创建系统用户 ${MCP_USER}")
            FAIL=$(( FAIL + 1 ))
            return
        fi
    fi

    # 依赖：按 requirements.txt 确保 mcp 已装（已装则 pip 是 no-op）
    local py
    py="$(pick_python)"
    if [ -n "${py}" ]; then
        if "${py}" -c 'import mcp' >/dev/null 2>&1; then
            printf '  %b MCP 依赖已就绪\n' "${C_GREEN}[ OK ]${C_RESET}"
            PASS=$(( PASS + 1 ))
        else
            log_info "安装 MCP 依赖（services/mcp/requirements.txt）..."
            if "${py}" -m pip install -q -r "${MCP_DIR}/requirements.txt" >/dev/null 2>&1; then
                printf '  %b MCP 依赖安装完成\n' "${C_GREEN}[ OK ]${C_RESET}"
                PASS=$(( PASS + 1 ))
            else
                printf '  %b MCP 依赖安装失败\n' "${C_RED}[FAIL]${C_RESET}"
                DETAILS+=("pip install -r services/mcp/requirements.txt 失败")
                FAIL=$(( FAIL + 1 ))
                return
            fi
        fi
    fi

    # 单元文件：内容变了才重启（避免无谓抖动）
    local changed=0
    if [ ! -f "${MCP_UNIT_DST}" ] || ! cmp -s "${MCP_UNIT_SRC}" "${MCP_UNIT_DST}"; then
        install -m 0644 "${MCP_UNIT_SRC}" "${MCP_UNIT_DST}"
        changed=1
    fi

    if [ "${changed}" -eq 1 ]; then
        systemctl daemon-reload
        printf '  %b 已安装/更新单元文件\n' "${C_GREEN}[ OK ]${C_RESET}"
        PASS=$(( PASS + 1 ))
    else
        printf '  %b 单元文件无变化\n' "${C_GREEN}[ OK ]${C_RESET}"
        PASS=$(( PASS + 1 ))
    fi

    systemctl enable data-platform-mcp >/dev/null 2>&1 || true
    systemctl restart data-platform-mcp >/dev/null 2>&1 || true

    # 等端口就绪（最多 20s）—— "启动了"不等于"能服务"
    local waited=0
    while [ "${waited}" -lt 20 ]; do
        if [ "$(http_code "${MCP_HEALTH}")" = "200" ]; then
            break
        fi
        sleep 1
        waited=$(( waited + 1 ))
    done
    check "MCP /healthz 就绪（等待 ${waited}s）" "200" "$(http_code "${MCP_HEALTH}")"
}

# ============================================================
# 主流程
# ============================================================
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} Sprint 10 验收：MCP（只读数据能力经 MCP 暴露）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    # !! 顺序很重要：preflight 必须在 load_env **之前** !!
    #   `load_env` 自己会在 .env 缺失时直接 exit 1，那时 preflight 的说明
    #   就永远打不出来，人只会看到一句"找不到 .env"而不知道该先做什么。
    preflight
    load_env

    deploy_mcp
    step_services
    step_capability_surface
    step_no_credentials
    step_readonly_boundary
    step_e2e_consistency
    step_regression

    summary
}

main "$@" < /dev/null
exit $?
