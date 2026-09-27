#!/usr/bin/env bash
# ============================================================
# scripts/deploy-monitoring.sh — 部署 Prometheus + Grafana（Sprint 11）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/deploy-monitoring.sh                 # 全流程（推荐）
#   bash scripts/deploy-monitoring.sh --no-nginx      # 只起容器，不动 Nginx
#   bash scripts/deploy-monitoring.sh --no-pull       # 不拉镜像（离线/已缓存时）
#   bash scripts/deploy-monitoring.sh --recreate      # 强制重建容器（改了 provisioning 后用）
#
# 步骤（7 步）：
#   1  前置检查（.env / 镜像 / 端口 / 内存）
#   2  口令供给（GRAFANA_ADMIN_PASSWORD 缺失则随机生成并写入 .env）
#   3  镜像拉取（版本在本脚本里是**断言**，不是提示）
#   4  compose 语法校验（**失败立即退出，不动运行中的服务**）
#   5  启动两个容器并等到 healthy
#   6  验证（各容器内自探 / 宿主机回环 / Doris 数据源真的能查到数）
#   7  Nginx 反代 /grafana/ 与 /metrics/（**先备份、nginx -t 失败自动还原**）
#
# ------------------------------------------------------------
# 为什么 compose 校验失败要"立刻退出且不启动"（而不是先 up 再修）
# ------------------------------------------------------------
#   本次改动是**追加**两个服务。若追加的段落有 YAML 语法错误，
#   `docker compose up -d` 会拒绝解析**整个文件** ——
#   也就是说，一次写坏的追加会让已经在跑的 12 个容器一起失去编排能力
#   （compose ps / compose logs 全部报同一句解析错误），
#   排查现场会瞬间从"监控没起来"变成"整个平台看起来都坏了"。
#   所以：先 `config --quiet` 校验，通过才 up。
#
# ------------------------------------------------------------
# 为什么 Nginx 一定要"先备份 + nginx -t 失败自动还原"
# ------------------------------------------------------------
#   这正是 scripts/deploy-web.sh 的做法，也是 AGENTS.md 15.7 的硬规范第 3 条：
#   不还原的话，机器会停在"文件是坏的、进程还在跑旧的"这种最尴尬的状态，
#   下一次 nginx 重启就直接起不来（把看板、API、Agent、Airflow 一起带走）。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

PROM_IMAGE="prom/prometheus:v3.13.3"
GRAFANA_IMAGE="grafana/grafana:13.2.2"

DO_NGINX=1
DO_PULL=1
RECREATE=0
FAILED=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-nginx) DO_NGINX=0 ;;
        --no-pull)  DO_PULL=0 ;;
        --recreate) RECREATE=1 ;;
        -h|--help)  sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) log_error "未知参数：$1"; exit 2 ;;
    esac
    shift
done

step() { printf '\n%b\n' "${C_BOLD}▶ $*${C_RESET}"; }
ok()   { printf '  %b %s\n' "${C_GREEN}[ OK ]${C_RESET}" "$*"; }
bad()  { printf '  %b %s\n' "${C_RED}[FAIL]${C_RESET}" "$*"; FAILED=1; }
warn() { printf '  %b %s\n' "${C_YELLOW}[WARN]${C_RESET}" "$*"; }

envv() { grep -E "^$1=" "${REPO_ROOT}/.env" 2>/dev/null | head -1 | cut -d= -f2-; }

# ------------------------------------------------------------
# 1. 前置检查
# ------------------------------------------------------------
precheck() {
    step "1/7 前置检查"

    if [ -f "${REPO_ROOT}/.env" ]; then
        ok ".env 存在"
    else
        bad ".env 不存在（先 cp .env.example .env）"
        return 1
    fi

    # 监控预算：512 MB。这里是"声明"，容器起来后另有实测取证。
    local budget=512 used
    used=$(( ${PROMETHEUS_MEM_LIMIT_MB:-256} + ${GRAFANA_MEM_LIMIT_MB:-256} ))
    if [ "${used}" -le "${budget}" ]; then
        ok "内存预算：Prometheus 256m + Grafana 256m = ${used}m（上限 ${budget}m）"
    else
        bad "内存预算超限：${used}m > ${budget}m"
    fi

    local avail
    avail="$(free -m | awk '/^Mem:/ {print $7}')"
    printf '  宿主机可用内存： %s MB\n' "${avail}"
    if [ "${avail}" -lt 1024 ]; then
        bad "可用内存过低（${avail} MB < 1024 MB）—— 先等批处理跑完，不要硬起监控"
        return 1
    fi
    ok "可用内存足够启动监控（预计占用 <= 512 MB）"

    # 端口占用：9090 / 3001 必须没被**别的**进程占
    #
    # !! 为什么不能简单地"端口开着就警告" !!
    #   本脚本是幂等的，重复执行时这两个端口**理应**已经被自己的容器占着。
    #   第一版每跑一次都报两条 WARN，看久了就会习惯性忽略 WARN ——
    #   而 WARN 一旦被忽略，真正的问题也会被忽略。
    #   所以这里先排除"是本项目监控容器自己在监听"的情况。
    local p owner
    for p in 9090 3001; do
        if ss -lntp 2>/dev/null | grep -q ":${p} "; then
            owner="$(docker ps --filter "publish=${p}" --format '{{.Names}}' 2>/dev/null | head -1)"
            if [ -n "${owner}" ]; then
                ok "端口 ${p} 已由本项目容器 ${owner} 监听（幂等重跑的正常状态）"
            else
                warn "端口 ${p} 被**非本项目**进程占用 —— 需改 .env 里的 HOST_PORT 映射"
            fi
        else
            ok "端口 ${p} 空闲"
        fi
    done
}

# ------------------------------------------------------------
# 2. 口令供给
# ------------------------------------------------------------
supply_secret() {
    step "2/7 口令供给（凭据只来自 .env，AGENTS.md 7.2）"

    if [ -n "$(envv GRAFANA_ADMIN_PASSWORD)" ]; then
        ok "GRAFANA_ADMIN_PASSWORD 已存在于 .env（不回显）"
    else
        # 为什么由脚本生成而不是写在 .env.example 里：
        #   .env.example 是**进仓库**的模板，只能放 change_me_* 占位符。
        #   生成 32 位随机口令写进 .env（.gitignore 已忽略）才是合规做法。
        local pw
        pw="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)"
        {
            printf '\n# ------------------------------------------------------------\n'
            printf '# Sprint 11 — 监控（由 scripts/deploy-monitoring.sh 生成，勿手工改成弱口令）\n'
            printf '# ------------------------------------------------------------\n'
            printf 'GRAFANA_ADMIN_USER=admin\n'
            printf 'GRAFANA_ADMIN_PASSWORD=%s\n' "${pw}"
            printf 'PROMETHEUS_HOST_PORT=9090\n'
            printf 'GRAFANA_HOST_PORT=3001\n'
        } >> "${REPO_ROOT}/.env"
        # 权限与既有约定一致：640 root:dpapi（不是 600）。
        #   为什么不是 600：.env 需要被数据服务/Agent 的 systemd 单元读取，
        #   它们以 dpapi 组身份运行；600 会让这些单元读不到新追加的变量。
        #   若 dpapi 组不存在（例如在别的机器上首次执行），退化为 640 root:root
        #   并在日志里说明 —— 静默用一个更宽的权限是不允许的。
        if getent group dpapi >/dev/null 2>&1; then
            chown root:dpapi "${REPO_ROOT}/.env" 2>/dev/null || true
            chmod 640 "${REPO_ROOT}/.env"
        else
            chmod 640 "${REPO_ROOT}/.env"
            printf '  %b 未找到 dpapi 组，.env 权限设为 640 root:root（请核对 systemd 单元的读取身份）\n' \
                "${C_YELLOW}[WARN]${C_RESET}"
        fi
        ok "已生成 GRAFANA_ADMIN_PASSWORD（32 位随机）并写入 .env（640，不回显）"
        printf '     查看方式： grep -E "^GRAFANA_ADMIN_PASSWORD=" %s/.env\n' "${REPO_ROOT}"
    fi

    # --------------------------------------------------------
    # !! .env 权限：必须是 640 + 属组 dpapi，不能是 600 !!
    #
    # 这是一次**真实的跨服务故障**，值得完整记下来：
    #   本 Sprint 最初在这里写的是 `chmod 600 .env`（"顺手收紧权限"看起来很稳妥）。
    #   但 .env 不是只有一个读者：
    #     - 数据服务 systemd 单元以 dpapi 组身份运行 → 需要**组读**
    #     - 数据问答 Agent 以 dpagent 用户运行（dpapi 组成员）→ 同样需要组读
    #   600 只有 root 可读 → **Agent 直接起不来**，报
    #     PermissionError: /opt/data-platform/.env
    #   而当时我正在另一个 Sprint 的工作里，症状出现在"别人负责的服务"上 ——
    #   并行改动引发跨服务故障时，**故障现场与改动现场不在一起**，最难关联。
    #
    #   所以这里不只"生成时设对"，而是**每次部署都校正一次**：
    #   权限属于"别人也会依赖的共享状态"，不是本脚本的一次性副作用。
    # --------------------------------------------------------
    local env_mode env_group
    env_mode="$(stat -c '%a' "${REPO_ROOT}/.env" 2>/dev/null || echo '?')"
    env_group="$(stat -c '%G' "${REPO_ROOT}/.env" 2>/dev/null || echo '?')"
    if [ "${env_mode}" = "640" ] && [ "${env_group}" = "dpapi" ]; then
        ok ".env 权限正确： 640 root:dpapi（数据服务与 Agent 都能读）"
    else
        if getent group dpapi >/dev/null 2>&1; then
            chown root:dpapi "${REPO_ROOT}/.env" 2>/dev/null || true
            chmod 640 "${REPO_ROOT}/.env"
            ok ".env 权限已校正： ${env_mode} ${env_group} → 640 root:dpapi"
        else
            chmod 640 "${REPO_ROOT}/.env"
            warn "未找到 dpapi 组，.env 设为 640 root:root —— 请核对 systemd 单元的读取身份"
        fi
    fi

    # 只读数据源口令：Grafana 面板靠它读 Doris
    if [ -n "$(envv API_DORIS_PASSWORD)" ]; then
        ok "API_DORIS_PASSWORD 已存在（Grafana 数据源用只读账号 $(envv API_DORIS_USER)，非 root）"
    else
        bad "缺少 API_DORIS_PASSWORD —— Grafana 面板将查不到业务数据"
        printf '     先跑： bash scripts/install-web.sh（会创建只读账号并写入 .env）\n'
    fi

    # Grafana 的固定 IP：prometheus 的 extra_hosts 与 grafana 的 ipv4_address
    # 都引用它，缺失时 compose 会退到默认值；但显式写进 .env 更利于排查。
    if [ -n "$(envv GRAFANA_IP)" ]; then
        ok "GRAFANA_IP 已存在： $(envv GRAFANA_IP)"
    else
        # !! 不要在动态分配区里挑 IP !!
        #   实测：把这个值写成 .12（紧邻 Doris 的 .10/.11，看着很自然），
        #   结果 mysql 恰好被 Docker 分到了 .12，Grafana 直接创建失败并报
        #   "Address already in use"。取 .20 远离动态段。
        printf '\n# Sprint 11 — Grafana 固定 IP（远离 Docker 动态分配段，见 .env.example 说明）\nGRAFANA_IP=172.28.0.20\n' >> "${REPO_ROOT}/.env"
        ok "已写入 GRAFANA_IP=172.28.0.20"
    fi

    # 端口映射项：缺失时 compose 用默认值，但显式写下来便于运维改
    local key
    for key in PROMETHEUS_HOST_PORT GRAFANA_HOST_PORT; do
        if [ -n "$(envv "${key}")" ]; then
            ok "${key} 已存在： $(envv "${key}")"
        else
            case "${key}" in
                PROMETHEUS_HOST_PORT) printf '%s=9090\n' "${key}" >> "${REPO_ROOT}/.env" ;;
                GRAFANA_HOST_PORT)    printf '%s=3001\n' "${key}" >> "${REPO_ROOT}/.env" ;;
            esac
            ok "已写入 ${key}"
        fi
    done
}

# ------------------------------------------------------------
# 3. 镜像
# ------------------------------------------------------------
pull_images() {
    step "3/7 镜像（版本已钉住，禁止 latest）"

    local img
    for img in "${PROM_IMAGE}" "${GRAFANA_IMAGE}"; do
        if docker image inspect "${img}" >/dev/null 2>&1; then
            ok "已存在： ${img}"
            continue
        fi
        if [ "${DO_PULL}" -eq 0 ]; then
            bad "本地没有 ${img} 且指定了 --no-pull"
            continue
        fi
        printf '  拉取 %s ...\n' "${img}"
        if docker pull "${img}" >/tmp/deploy-monitoring-pull.log 2>&1; then
            ok "已拉取： ${img}"
        else
            bad "拉取失败： ${img}（见 /tmp/deploy-monitoring-pull.log）"
            printf '     本机直连 Docker Hub 不通（实测超时），靠 /etc/docker/daemon.json 的镜像加速；\n'
            printf '     若加速器也失效，可临时换一个镜像前缀后重试。\n'
        fi
    done

    # 断言：版本字符串必须与脚本里写的一致（防止 .env 里被改成别的版本）
    local pv gv
    pv="$(envv PROMETHEUS_VERSION)"; pv="${pv:-v3.13.3}"
    gv="$(envv GRAFANA_VERSION)"; gv="${gv:-13.2.2}"
    [ "prom/prometheus:${pv}" = "${PROM_IMAGE}" ] \
        && ok "Prometheus 版本一致： ${PROM_IMAGE}" \
        || warn "PROMETHEUS_VERSION=${pv} 与脚本钉住的 ${PROM_IMAGE} 不一致（以 compose 为准）"
    [ "grafana/grafana:${gv}" = "${GRAFANA_IMAGE}" ] \
        && ok "Grafana 版本一致： ${GRAFANA_IMAGE}" \
        || warn "GRAFANA_VERSION=${gv} 与脚本钉住的 ${GRAFANA_IMAGE} 不一致（以 compose 为准）"
}

# ------------------------------------------------------------
# 4. compose 语法校验（失败即退出）
# ------------------------------------------------------------
validate_compose() {
    step "4/7 docker compose 语法校验（失败则**不动运行中的服务**）"

    if compose config --quiet 2>/tmp/deploy-monitoring-compose.log; then
        ok "compose 解析通过"
    else
        printf '\n'
        bad "compose 解析失败 —— 立即停止，未做任何变更"
        cat /tmp/deploy-monitoring-compose.log
        printf '\n  回退方式：把 docker-compose.yml 里 Sprint 11 追加的段落删掉即可\n'
        printf '  （既有服务一个字段都没被改动，删掉追加段即回到原状）\n'
        exit 1
    fi

    # 额外断言：解析后的配置里必须能看到这两个服务与它们的 mem_limit
    local rendered
    rendered="$(compose config 2>/dev/null)"
    printf '%s' "${rendered}" | grep -q '^  prometheus:' \
        && ok "解析结果含 prometheus 服务" \
        || bad "解析结果里找不到 prometheus 服务"
    printf '%s' "${rendered}" | grep -q '^  grafana:' \
        && ok "解析结果含 grafana 服务" \
        || bad "解析结果里找不到 grafana 服务"
    # 注意 mem_limit 在渲染结果里是**带引号的字符串**（`mem_limit: "268435456"`），
    #   所以模式里要容忍引号 —— 第一版写 `mem_limit: 268435456`（无引号），
    #   结果是 compose 明明配对了却报 WARN，属于"自己吓自己"的假警报。
    #   这里同时数一下出现的次数（应为 2：prometheus + grafana）。
    local memcnt
    memcnt="$(printf '%s' "${rendered}" | grep -c 'mem_limit: *"*268435456' || true)"
    if [ "${memcnt:-0}" -ge 2 ]; then
        ok "mem_limit 已生效： 2 × 256m = 512m（budget 内）"
    else
        warn "渲染结果里只有 ${memcnt:-0} 处 268435456 —— 请确认 .env 未覆盖 PROMETHEUS_MEM_LIMIT/GRAFANA_MEM_LIMIT"
    fi
}

# ------------------------------------------------------------
# 5. 启动
# ------------------------------------------------------------
start_services() {
    step "5/7 启动监控容器"

    local args=(up -d prometheus grafana)
    [ "${RECREATE}" -eq 1 ] && args=(up -d --force-recreate prometheus grafana)

    if compose "${args[@]}" 2>&1 | sed 's/^/     /'; then
        ok "compose up 已执行"
    else
        bad "compose up 失败"
        return 1
    fi

    # 等到 healthy（区分"容器已启动"与"服务已就绪"）
    local i name status
    for name in prometheus grafana; do
        for i in $(seq 1 30); do
            status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${name}" 2>/dev/null || echo missing)"
            [ "${status}" = "healthy" ] && break
            sleep 3
        done
        if [ "${status}" = "healthy" ]; then
            ok "${name} healthy（等待 $(( i * 3 ))s 内）"
        else
            bad "${name} 未达到 healthy（当前 ${status}）—— 见 docker compose logs ${name}"
        fi
    done
    return 0
}

# ------------------------------------------------------------
# 6. 验证
# ------------------------------------------------------------
verify_services() {
    step "6/7 验证（容器内自探 + 宿主机回环 + 数据源真的能查到数）"

    local code

    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:9090/-/healthy || echo 000)"
    [ "${code}" = "200" ] && ok "Prometheus 回环 /-/healthy → 200" || bad "Prometheus /-/healthy → ${code}"

    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:3001/api/health || echo 000)"
    [ "${code}" = "200" ] && ok "Grafana 回环 /api/health → 200" || bad "Grafana /api/health → ${code}"

    # 抓取目标数：必须是 4（prometheus/doris-fe/doris-be/grafana）
    local targets up_count
    targets="$(curl -s --max-time 10 'http://127.0.0.1:9090/api/v1/targets?state=active' 2>/dev/null || true)"
    up_count="$(printf '%s' "${targets}" | grep -o '"health":"up"' | wc -l | tr -d '[:space:]')"
    if [ "${up_count:-0}" -ge 4 ]; then
        ok "Prometheus 已抓取到的 up 目标数： ${up_count}"
    else
        warn "Prometheus up 目标数 ${up_count:-0} < 4（刚启动时正常，30s 后再看）"
    fi

    # 关键证据：Grafana 数据源**真的**能从 Doris 读到业务数据
    #   只探活是不够的 —— "容器 healthy 但数据源连不上"是最常见的假成功
    local row
    row="$(compose exec -T doris-be mysql -h 172.28.0.10 -P 9030 -uroot -N \
            -e 'SELECT COUNT(*) FROM lakehouse_ads.ads_batch_trade_1d;' 2>/dev/null | tr -d '[:space:]')"
    if [ -n "${row}" ] && [ "${row}" -gt 0 ] 2>/dev/null; then
        ok "Doris 侧数据在位（ads_batch_trade_1d = ${row} 行）—— 面板有东西可画"
    else
        warn "ads_batch_trade_1d 读不到行数（面板会显示 No data）"
    fi

    # 让 Grafana 自己回答"数据源健康吗"（比我们猜更可信）
    #
    # 口令从 .env 文件里现读（load_env 只 export 了它解析到的键，
    # 而 .env 里可能还有其他来源写入的行；直接读文件最稳），
    # 并且**不回显**（不把口令拼进命令行参数）。
    # !! 命令替换的收尾 `)"` 不能少 !!
    #   实测踩坑：这里曾经漏掉结尾的 `"`，于是 `$(` 没有闭合。
    #   bash **不会**在那一行报错 —— 它会一路读到文件末尾才报
    #     `line 548: unexpected EOF while looking for matching '"'`，
    #   把故障指向文件的最后一行，而真正的问题在第 364 行。
    #   所以"整份脚本的语法自检"要放在改完之后立刻做：
    #     bash -n scripts/deploy-monitoring.sh
    local admin_pw
    admin_pw="$(envv GRAFANA_ADMIN_PASSWORD)"
    local ds_health
    ds_health="$(compose exec -T -e GP="${admin_pw}" grafana sh -c \
        'wget -q -O - "http://${GRAFANA_ADMIN_USER:-admin}:$GP@127.0.0.1:3000/api/datasources" 2>/dev/null' 2>/dev/null || true)"
    if printf '%s' "${ds_health}" | grep -q 'doris-mysql'; then
        ok "Grafana 已供给数据源 doris-mysql（经 API 实证）"
    else
        warn "未能经 API 列出数据源（口令/网络原因），改由 verify-sprint-11.sh 复核"
    fi
}

# ------------------------------------------------------------
# 7. Nginx 反代
# ------------------------------------------------------------
NGINX_SITE="/etc/nginx/sites-available/data-platform.conf"
NGINX_BLOCK_MARKER="# Sprint 11 — Prometheus / Grafana"

# 供 Nginx 定位 Grafana 与 Prometheus 的 upstream。
#   为什么用**宿主回环端口**而不是容器名：
#   Nginx 跑在**宿主机**上（服务层部署原则，AGENTS.md 2.1），
#   它不在 data-platform 网络里，因此解析不了容器名 grafana。
#   而这两个容器的端口本来就只绑在 127.0.0.1 上，正好是给宿主 Nginx 用的。
configure_nginx() {
    step "7/7 Nginx 反代 /grafana/ 与 /metrics/"

    if [ ! -f "${NGINX_SITE}" ]; then
        bad "找不到站点文件 ${NGINX_SITE}"
        return 1
    fi

    if grep -q "${NGINX_BLOCK_MARKER}" "${NGINX_SITE}"; then
        ok "反代段落已存在（幂等，跳过）"
        nginx -t >/tmp/deploy-monitoring-nginx.log 2>&1 && ok "nginx -t 通过" || { bad "nginx -t 失败"; cat /tmp/deploy-monitoring-nginx.log; }
        return 0
    fi

    # !! 先备份（AGENTS.md 15.7 硬规范第 3 条）!!
    cp -a "${NGINX_SITE}" "${NGINX_SITE}.sprint11.bak"

    if ! python3 - "${NGINX_SITE}" <<'PY'
import sys

path = sys.argv[1]
block = r'''
    # ========================================================
    # Sprint 11 — Prometheus / Grafana
    #
    # 为什么用**宿主回环端口**而不是容器名：
    #   Nginx 跑在宿主机上，不在 data-platform 容器网络里，
    #   解析不了 grafana / prometheus 这类服务名。
    #   而这两个容器的端口本来就只绑 127.0.0.1，正好由宿主 Nginx 消费。
    #
    # !! proxy_pass 结尾带不带 "/" 的区别（Sprint 4 踩过同类坑）!!
    #   /grafana/  → 结尾**不带** /：保留前缀。因为 Grafana 侧
    #                serve_from_sub_path=true + root_url 已声明 /grafana/，
    #                它自己就期望收到带前缀的请求。
    #   /metrics/  → 结尾**带** /：剥掉前缀。因为 Prometheus 的
    #                --web.external-url 只影响自己生成的链接，
    #                实际路由前缀是 --web.route-prefix=/（即根路径），
    #                所以必须把 /metrics/ 变成 / 再转发。
    #   写反的症状分别是"登录页能开、一登录 404"与"页面所有资源 404"。
    # ========================================================

    # Grafana：/grafana/ → 127.0.0.1:3001
    location /grafana/ {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 10s;
        proxy_read_timeout    300s;
        proxy_send_timeout    300s;
    }

    # 不带斜杠的 /grafana 也要跳转，否则相对路径会解析到站点根
    location = /grafana {
        return 301 /grafana/;
    }

    # Prometheus：/metrics/ → 127.0.0.1:9090/（注意结尾的 / 会剥掉前缀）
    #
    # 为什么把 Prometheus 暴露到公网：验收要给出一个**从浏览器直接可看**的
    # 原始指标入口（Grafana 面板是加工后的视图）。它没有任何业务数据，
    # 端口本身也只绑回环，唯一的对外通道就是这一条 location。
    # 只读（GET/HEAD/OPTIONS），写操作在 Nginx 层就被拒。
    location /metrics/ {
        proxy_pass http://127.0.0.1:9090/;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        limit_except GET HEAD OPTIONS {
            deny all;
        }

        proxy_connect_timeout 5s;
        proxy_read_timeout    60s;
    }

    location = /metrics {
        return 301 /metrics/;
    }

'''

with open(path, encoding='utf-8') as fh:
    text = fh.read()

# 插到最后一个 server 块的收尾大括号之前
idx = text.rfind('}')
if idx < 0:
    sys.exit('找不到 server 块的收尾大括号')

text = text[:idx] + block + text[idx:]
with open(path, 'w', encoding='utf-8') as fh:
    fh.write(text)
PY
    then
        bad "插入反代段落失败"
        mv -f "${NGINX_SITE}.sprint11.bak" "${NGINX_SITE}"
        return 1
    fi

    if nginx -t >/tmp/deploy-monitoring-nginx.log 2>&1; then
        systemctl reload nginx
        rm -f "${NGINX_SITE}.sprint11.bak"
        ok "Nginx 配置已更新并 reload（nginx -t 通过）"
    else
        bad "nginx -t 失败 —— 自动还原原配置"
        cat /tmp/deploy-monitoring-nginx.log | sed 's/^/     /'
        mv -f "${NGINX_SITE}.sprint11.bak" "${NGINX_SITE}"
        nginx -t >/dev/null 2>&1 && ok "已还原，nginx 仍按原配置运行（服务未中断）"
        printf '     注意：还原只回滚了 Nginx，两个监控容器仍在跑（不影响既有站点）\n'
        return 1
    fi
}

# ------------------------------------------------------------
main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 部署监控（Prometheus + Grafana）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    require_docker

    # 说明：每一步内部都用 bad/warn 记账，这里刻意关掉 errexit，
    # 让"某一步失败"也能走完全流程并把后续证据打出来 ——
    # 否则第一个非零返回就会中断，剩下的检查全部看不到。
    set +e

    precheck || { log_error "前置检查未通过，已停止"; return 1; }
    supply_secret
    pull_images
    validate_compose
    start_services
    verify_services
    [ "${DO_NGINX}" -eq 1 ] && configure_nginx

    local ip
    ip="$(public_ip)"

    printf '\n%b\n' "${C_BOLD}=====================================${C_RESET}"
    if [ "${FAILED}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} 监控部署完成${C_RESET}"
        printf '\n  公网入口（无 VPN/代理时）：\n'
        printf '    Grafana     %s://%s/grafana/\n' "$(site_scheme)" "${ip}"
        printf '    Prometheus  %s://%s/metrics/\n' "$(site_scheme)" "${ip}"
        printf '  仅本机（绑回环，公网不可达）：\n'
        printf '    Prometheus  http://127.0.0.1:9090/\n'
        printf '    Grafana     http://127.0.0.1:3001/\n'
        printf '\n  镜像版本： prom/prometheus:v3.13.3 / grafana/grafana:13.2.2\n'
        printf '  内存上限： 256m + 256m = 512m（本机实时链路常驻约 13.7 GB，务必守住）\n'
    else
        printf '%b\n' "${C_RED}${C_BOLD} 监控部署存在问题，请按上方提示排查${C_RESET}"
        return 1
    fi
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '\n'
    return 0
}

main "$@" < /dev/null
exit $?
