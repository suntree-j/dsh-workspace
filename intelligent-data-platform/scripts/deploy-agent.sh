#!/usr/bin/env bash
# ============================================================
# scripts/deploy-agent.sh — 部署数据问答 Agent（Sprint 7）
# ============================================================
#
# 用法（服务器上，仓库根目录，需要 root）：
#   bash scripts/deploy-agent.sh              # 首次安装或更新代码后重跑
#   bash scripts/deploy-agent.sh --no-restart # 只校验配置、不重启服务
#
# 做什么（幂等，可反复执行）：
#   1. 建专用系统用户 dpagent（不用 root 跑服务）
#   2. 建独立 venv：/opt/data-platform/.venv-agent
#      （与数据服务的 .venv 分开 —— 两边的依赖集合不同，
#        共用会让"升 Agent 依赖"意外影响看板接口）
#   3. 安装 services/agent/requirements.txt
#   4. 校验 .env 里的 LLM 配置（缺 Key 只告警不中断，见下方说明）
#   5. 安装 systemd 单元并启动
#   6. 校验 Nginx 配置并 reload（/data/agent/ 的反代在其中）
#   7. 自检：/health 必须返回 200
#
# !! 为什么缺 LLM_API_KEY 不中断部署 !!
#   申请 Key 需要人工操作（平台注册 + 实名）。若强制要求先有 Key 才能装服务，
#   部署会被卡住，而且"装没装上"这件事变得不可验证。
#   这里改为：先装好服务，/health 明确报告 llm.configured=false，
#   /ask 会返回 503 并给出配置步骤。这样"基础设施就绪"与"凭据就绪"
#   两件事可以分开推进，且状态始终可见。
#
# 权限设计：
#   .env 是 root:dpapi 0640（见 install-web.sh），里面同时有数据库口令与 LLM Key。
#   dpagent 需要**读**它才能拿到 LLM_API_KEY —— 因此把 dpagent 加入 dpapi 组。
#   这样它只能读、不能改（写权限仍只在 root）。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

AGENT_USER="dpagent"
VENV_DIR="${REPO_ROOT}/.venv-agent"
UNIT_NAME="data-platform-agent"
UNIT_SRC="${REPO_ROOT}/deploy/systemd/data-platform-agent.service"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}.service"
ENV_FILE="${REPO_ROOT}/.env"

RESTART=1

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --no-restart) RESTART=0; shift ;;
            -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
            *) log_error "未知参数：$1"; exit 2 ;;
        esac
    done
}

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log_error "需要 root 权限： sudo bash scripts/deploy-agent.sh"
        exit 1
    fi
}

# ------------------------------------------------------------
# 1. 系统用户
# ------------------------------------------------------------
ensure_user() {
    if id -u "${AGENT_USER}" >/dev/null 2>&1; then
        log_ok "系统用户 ${AGENT_USER} 已存在"
    else
        useradd --system --create-home --shell /usr/sbin/nologin "${AGENT_USER}"
        log_ok "已创建系统用户 ${AGENT_USER}"
    fi

    # 需要读 .env（里面有 LLM_API_KEY）。.env 属于 dpapi 组，0640。
    if getent group dpapi >/dev/null 2>&1; then
        if id -nG "${AGENT_USER}" | tr ' ' '\n' | grep -qx dpapi; then
            log_ok "${AGENT_USER} 已在 dpapi 组（可读 .env）"
        else
            usermod -aG dpapi "${AGENT_USER}"
            log_ok "已把 ${AGENT_USER} 加入 dpapi 组（只读 .env）"
        fi
    else
        log_warn "找不到 dpapi 组：请先执行 bash scripts/install-web.sh"
    fi
}

# ------------------------------------------------------------
# 2. 独立 venv
# ------------------------------------------------------------
ensure_venv() {
    if [ ! -x "${VENV_DIR}/bin/python" ]; then
        log_info "创建独立虚拟环境 ${VENV_DIR} ..."
        python3 -m venv "${VENV_DIR}"
    fi
    log_info "安装 Agent 依赖（services/agent/requirements.txt）..."
    "${VENV_DIR}/bin/pip" install -q --disable-pip-version-check --upgrade pip
    "${VENV_DIR}/bin/pip" install -q --disable-pip-version-check \
        -r "${REPO_ROOT}/services/agent/requirements.txt"
    log_ok "依赖就绪：$("${VENV_DIR}/bin/python" -V)"
}

# ------------------------------------------------------------
# 3. 配置校验
# ------------------------------------------------------------
check_env() {
    if [ ! -f "${ENV_FILE}" ]; then
        log_error "找不到 ${ENV_FILE}"
        exit 1
    fi

    # 只用 grep 取值，不 source（.env 里可能有带空格的值）
    local key model base
    key="$(grep -E '^LLM_API_KEY=' "${ENV_FILE}" | head -1 | cut -d= -f2- || true)"
    model="$(grep -E '^LLM_MODEL=' "${ENV_FILE}" | head -1 | cut -d= -f2- || true)"
    base="$(grep -E '^LLM_BASE_URL=' "${ENV_FILE}" | head -1 | cut -d= -f2- || true)"

    printf '  LLM 模型： %s\n' "${model:-（未设置，将用默认 deepseek-flash）}"
    printf '  LLM 地址： %s\n' "${base:-（未设置，将用默认 https://api.deepseek.com）}"

    if [ -z "${key}" ] || [ "${key}" = "change_me_deepseek_api_key" ]; then
        log_warn "LLM_API_KEY 未配置（或仍是占位符）"
        printf '    服务会正常启动，但 /ask 会返回 503 并给出配置步骤。\n'
        printf '    配置方法： 在 %s 中设置 LLM_API_KEY，然后\n' "${ENV_FILE}"
        printf '               systemctl restart %s\n' "${UNIT_NAME}"
    else
        log_ok "LLM_API_KEY 已配置（%s...，长度 %s）" "${key:0:6}" "${#key}"
    fi
}

# ------------------------------------------------------------
# 4. systemd
# ------------------------------------------------------------
install_unit() {
    if [ ! -f "${UNIT_SRC}" ]; then
        log_error "找不到单元文件 ${UNIT_SRC}"
        exit 1
    fi
    install -m 644 "${UNIT_SRC}" "${UNIT_DST}"
    systemctl daemon-reload
    systemctl enable "${UNIT_NAME}" >/dev/null 2>&1
    log_ok "已安装并 enable ${UNIT_NAME}.service"
}

# ------------------------------------------------------------
# 5. Nginx
# ------------------------------------------------------------
reload_nginx() {
    if ! command -v nginx >/dev/null 2>&1; then
        log_warn "未安装 Nginx，跳过（/data/agent/ 将无法从外部访问）"
        return 0
    fi

    local site="/etc/nginx/sites-available/data-platform.conf"
    if ! diff -q "${REPO_ROOT}/deploy/nginx/data-platform.conf" "${site}" >/dev/null 2>&1; then
        cp "${REPO_ROOT}/deploy/nginx/data-platform.conf" "${site}"
        log_info "已更新 Nginx 站点配置"
    fi

    if nginx -t >/tmp/nginx-agent-test.log 2>&1; then
        systemctl reload nginx
        log_ok "Nginx 配置校验通过并已 reload"
    else
        log_error "Nginx 配置校验失败："
        cat /tmp/nginx-agent-test.log
        exit 1
    fi
}

# ------------------------------------------------------------
# 6. 自检
# ------------------------------------------------------------
selfcheck() {
    local port deadline ok=0
    port="$(grep -E '^AGENT_PORT=' "${ENV_FILE}" | head -1 | cut -d= -f2- || true)"
    port="${port:-8100}"

    deadline=$(( SECONDS + 60 ))
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        if curl -fsS --max-time 5 "http://127.0.0.1:${port}/health" >/tmp/agent-health.json 2>/dev/null; then
            ok=1
            break
        fi
        printf '\r  等待 Agent 就绪 ... (%ss)' "${SECONDS}"
        sleep 2
    done
    printf '\r\033[K'

    if [ "${ok}" -ne 1 ]; then
        log_error "Agent 未在 60 秒内就绪"
        printf '  排查： journalctl -u %s -n 50 --no-pager\n' "${UNIT_NAME}"
        return 1
    fi

    log_ok "Agent 健康检查通过： http://127.0.0.1:${port}/health"

    if command -v python3 >/dev/null 2>&1; then
        python3 - <<'PY' /tmp/agent-health.json || true
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
print(f"  状态： {data.get('status')}")
print(f"  数据服务： {'可达' if data.get('data_api', {}).get('ok') else '不可达'}"
      f" ({data.get('data_api', {}).get('detail', '')})")
llm = data.get("llm", {})
print(f"  LLM： {'已配置' if llm.get('configured') else '未配置'} "
      f"model={llm.get('model')} thinking={llm.get('thinking_mode')}")
PY
    fi
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 部署数据问答 Agent（Sprint 7）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    parse_args "$@"
    load_env
    require_root

    printf '\n%b\n' "${C_BOLD}▶ 1/6 系统用户${C_RESET}"
    ensure_user
    printf '\n%b\n' "${C_BOLD}▶ 2/6 独立虚拟环境与依赖${C_RESET}"
    ensure_venv
    printf '\n%b\n' "${C_BOLD}▶ 3/6 配置检查${C_RESET}"
    check_env
    printf '\n%b\n' "${C_BOLD}▶ 4/6 systemd 单元${C_RESET}"
    install_unit
    printf '\n%b\n' "${C_BOLD}▶ 5/6 Nginx${C_RESET}"
    reload_nginx

    if [ "${RESTART}" -eq 1 ]; then
        # !! 必须一并重启数据服务（实测踩坑）!!
        #   Sprint 7 给数据服务新增了 POST /query 路由。
        #   只重启 Agent 的话，数据服务仍在跑旧代码，
        #   Agent 提问时会收到 404 —— 而服务状态显示一切正常，
        #   极难定位（第一次部署就踩了这个坑）。
        #   重启数据服务是幂等的，且它无状态，代价只有几百毫秒。
        systemctl restart data-platform-api
        log_ok "已重启 data-platform-api（确保 /query 路由生效）"
        sleep 3

        systemctl restart "${UNIT_NAME}"
        log_ok "已重启 ${UNIT_NAME}"
    else
        log_info "按 --no-restart 跳过重启"
        return 0
    fi

    printf '\n%b\n' "${C_BOLD}▶ 6/6 自检${C_RESET}"
    selfcheck

    printf '\n'
    log_ok "Agent 部署完成"
    printf '  站内地址： %s://<服务器IP>/data/agent/health\n' "$(site_scheme)"
    printf '  接口文档： %s://<服务器IP>/data/agent/docs\n' "$(site_scheme)"
    printf '  提问示例：\n'
    printf '    curl -s -X POST http://127.0.0.1:%s/ask \\\n' "$(grep -E '^AGENT_PORT=' "${ENV_FILE}" | head -1 | cut -d= -f2- || echo 8100)"
    printf '      -H "Content-Type: application/json" \\\n'
    printf '      -d "{\\"question\\":\\"最近一周每天的 GMV 是多少？\\"}" | python3 -m json.tool\n'
    printf '  验收： bash scripts/verify-sprint-7.sh\n'
}

main "$@" < /dev/null
exit $?
