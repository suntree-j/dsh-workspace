#!/usr/bin/env bash
# ============================================================
# Doris BE 保活包装
#
# 背景（实测根因）：
#   apache/doris:be-4.1.4 的 entry_point.sh 流程为
#     main() { validate; parse; bash init_be.sh & ; check_be_status; ...; wait }
#   check_be_status 一旦检测到 BE 就绪就返回，随后 main() 结束、
#   容器 PID 1 退出，Docker 随即终止容器内的 doris_be 进程并按
#   restart 策略重启 —— 于是出现「每 ~15 秒重启一次」的循环。
#
#   证据：be.INFO 中每 ~15 秒出现一次完整初始化序列
#   （doris_main.cpp:413 version → JNI initialized → 监听 8040/9050/9060），
#   RestartCount 持续增长，且无 OOM（cgroup oom_kill=0、ExitCode=0）。
#
# 本包装脚本：在前台反复以 --console 启动 BE（Docker 可正常管理信号），
# 使容器保持存活。这是本地开发环境的务实处理方式；
# 上游修复后应移除本脚本并恢复使用镜像默认 entrypoint。
# ============================================================
set -u

DORIS_ROOT="${DORIS_ROOT:-/opt/apache-doris}"
BE_START="${DORIS_ROOT}/be/bin/start_be.sh"

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

# 交给原始初始化脚本完成注册与首次启动
bash /opt/apache-doris/be_entrypoint.sh "${FE_HOST}" &
INIT_PID=$!
echo "[keepalive] be_entrypoint.sh pid=${INIT_PID} (传入 FE_HOST=${FE_HOST})"

# 等待 BE 首次就绪
for _ in $(seq 1 60); do
  if bash -c 'exec 3<>/dev/tcp/127.0.0.1/9050' 2>/dev/null; then
    echo "[keepalive] BE 心跳端口就绪 $(date -Iseconds)"
    break
  fi
  sleep 2
done

# 前台保活：每次 BE 退出后重新拉起，避免容器 PID 1 退出
while true; do
  echo "[keepalive] 前台拉起 start_be.sh --console $(date -Iseconds)"
  "${BE_START}" --console
  rc=$?
  echo "[keepalive] start_be.sh 退出 rc=${rc} $(date -Iseconds)，3 秒后重试"
  sleep 3
done
