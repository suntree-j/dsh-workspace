#!/usr/bin/env bash
# ============================================================
# Doris BE 保活包装 + 初始化 SQL 补执行
#
# 背景（实测根因）：
#   apache/doris:be-4.1.4 的 entry_point.sh 流程为
#     main() { validate; parse; bash init_be.sh & ; check_be_status;
#              process_init_files /docker-entrypoint-initdb.d/* ; wait }
#   check_be_status 一旦检测到 BE 就绪立即返回，随后 main() 结束、
#   容器 PID 1 退出，Docker 随即终止 doris_be 并按 restart 策略重启。
#
#   !! 由此还带来一个隐蔽后果 !!
#   process_init_files（执行 sql/doris/*.sql）位于容器启动脚本的
#   另一条代码路径（在 BE 容器里由 entry_point.sh 负责），
#   在重启循环期间它从未被完整执行，导致 ecommerce 库与
#   test_connection 表从未创建。
#
# 本包装负责三件事：
#   1) 调用原始 be_entrypoint.sh 完成 BE 注册与启动；
#   2) 等待 BE 真正就绪后，**补执行 /docker-entrypoint-initdb.d 下的 SQL**；
#   3) 前台保活，使容器 PID 1 不退出（用 Doris 自带 --daemon 守护）。
#
# 上游修复后应移除本脚本，恢复镜像默认 entrypoint。
# ============================================================
set -u

DORIS_ROOT="${DORIS_ROOT:-/opt/apache-doris}"
BE_START="${DORIS_ROOT}/be/bin/start_be.sh"
HEARTBEAT_PORT=9050
WEB_PORT=8040
INITDB_DIR="/docker-entrypoint-initdb.d"
MAX_INIT_ATTEMPTS=5

be_is_up() {
    bash -c "exec 3<>/dev/tcp/127.0.0.1/${HEARTBEAT_PORT}" 2>/dev/null
}

be_web_up() {
    bash -c "exec 3<>/dev/tcp/127.0.0.1/${WEB_PORT}" 2>/dev/null
}

echo "[keepalive] Doris BE 保活包装启动 $(date -Iseconds)"

# ------------------------------------------------------------
# FE 地址解析
#
# 环境变量 FE_SERVERS 的格式为 name:ip:port（Doris 的 ELECTION 模式要求），
# 但 be_entrypoint.sh 的入参需要的是 **纯主机地址**，
# 若把 "fe1:172.28.0.10:9010" 直接传进去，会得到：
#   ERROR 2005 (HY000): Unknown MySQL server host 'fe1:172.28.0.10:9010'
# 因此这里用 POSIX 参数展开取出中间的 IP 部分。
# ------------------------------------------------------------
FE_SERVER_SPEC="${FE_SERVERS:-fe1:127.0.0.1:9010}"
FE_REST="${FE_SERVER_SPEC#*:}"      # 去掉 "fe1:"  -> "172.28.0.10:9010"
FE_HOST="${FE_REST%%:*}"            # 去掉 ":9010"  -> "172.28.0.10"
echo "[keepalive] FE_SERVERS=${FE_SERVER_SPEC} -> FE_HOST=${FE_HOST}"

# ------------------------------------------------------------
# 阶段 1：交给原始初始化脚本完成注册与首次启动
# ------------------------------------------------------------
bash /opt/apache-doris/be_entrypoint.sh "${FE_HOST}" &
INIT_PID=$!
echo "[keepalive] be_entrypoint.sh pid=${INIT_PID} (传入 FE_HOST=${FE_HOST})"

for _ in $(seq 1 120); do
    if be_is_up; then
        echo "[keepalive] BE 心跳端口就绪 $(date -Iseconds)"
        break
    fi
    sleep 2
done

# ------------------------------------------------------------
# 阶段 2：补执行初始化 SQL
#
# 幂等性：脚本内全部使用 IF NOT EXISTS / 先查后插，
#         容器重建重复执行是安全的。
# ------------------------------------------------------------
run_init_sql() {
    [ -d "${INITDB_DIR}" ] || return 0
    local ran=0

    # ---- .sql 文件：交给 mysql 客户端执行 ----
    for f in "${INITDB_DIR}"/*.sql; do
        [ -f "$f" ] || continue
        echo "[keepalive] 执行初始化 SQL：${f}"
        local attempt=1
        while [ "${attempt}" -le "${MAX_INIT_ATTEMPTS}" ]; do
            if mysql -h "${FE_HOST}" -P 9030 -uroot --connect-timeout=15 < "$f"; then
                echo "[keepalive] ${f} 执行成功（第 ${attempt} 次尝试）"
                ran=$((ran + 1))
                break
            fi
            echo "[keepalive] ${f} 第 ${attempt}/${MAX_INIT_ATTEMPTS} 次失败，10s 后重试"
            sleep 10
            attempt=$((attempt + 1))
        done
    done

    # ---- .sh 文件：用于幂等的数据写入（先查后插） ----
    for f in "${INITDB_DIR}"/*.sh; do
        [ -f "$f" ] || continue
        echo "[keepalive] 执行初始化脚本：${f}"
        if FE_HOST="${FE_HOST}" bash "$f"; then
            echo "[keepalive] ${f} 执行成功"
            ran=$((ran + 1))
        else
            echo "[keepalive] ${f} 执行失败（不影响容器存活）"
        fi
    done

    echo "[keepalive] 初始化目录处理完成，成功 ${ran} 个文件"
}

# 等 BE 的 HTTP 端口起来（说明 BE 已完成初始化并可对外服务）
for _ in $(seq 1 60); do
    be_web_up && break
    sleep 5
done

if be_is_up && be_web_up; then
    run_init_sql
else
    echo "[keepalive] BE 未就绪，跳过初始化 SQL（将由后续 restart 或人工处理）"
fi

# ------------------------------------------------------------
# 阶段 3：切换 Doris 自带守护模式并前台保活
#
# 为什么用 --daemon 而不是循环 --console：
#   BE 已在运行时 --console 会立即返回 rc=1（PID 文件已存在），
#   造成包装层每 3 秒空转刷日志。
#   start_be.sh --daemon 本身就是「启动 + 守护」脚本，
#   会在 BE 意外退出时自动重新拉起。
# ------------------------------------------------------------
echo "[keepalive] 切换到 Doris 自带守护模式 $(date -Iseconds)"
"${BE_START}" --daemon || true

while true; do
    if ! be_is_up; then
        echo "[keepalive] BE 未监听，尝试重新拉起 $(date -Iseconds)"
        "${BE_START}" --daemon || true
    fi
    sleep 15
done
