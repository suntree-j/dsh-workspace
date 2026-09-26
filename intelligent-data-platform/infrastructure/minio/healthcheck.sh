#!/bin/sh
# ============================================================
# MinIO 健康检查脚本（供 docker-compose healthcheck 使用）
# ============================================================
#
# !! 运行环境限制（实测） !!
#   minio 镜像基于 BusyBox，**不含 curl / wget / nc / sed / awk / grep / tar**，
#   只有 sh 内建、busybox 基础工具与 /usr/bin/mc。
#   因此健康检查只能使用 mc，不能用 curl。
#
# 为什么需要这个脚本而不是一行命令：
#   - `mc ready local` 在未配置 MC_HOST_local 时会打印
#     "Couldn't construct anonymous client" 但**返回退出码 0**，
#     会把不健康的服务误判为健康。
#   - `mc ls local` 在未配置别名时返回非 0，但容器自身的 shell
#     没有 MC_HOST_local（只有 minio-init 容器有），会一直失败。
#   因此这里显式用容器内的凭据构造别名，再执行 mc ls：
#   服务可用 -> 退出 0；服务异常 -> 非 0。
# ============================================================

set -eu

if [ -z "${MINIO_ROOT_USER:-}" ] || [ -z "${MINIO_ROOT_PASSWORD:-}" ]; then
    echo "MINIO_ROOT_USER / MINIO_ROOT_PASSWORD 未设置" >&2
    exit 1
fi

# POSIX 参数展开，避免依赖 sed
HOST=${MINIO_ENDPOINT:-http://localhost:9000}
HOST=${HOST#http://}
HOST=${HOST#https://}
[ -n "${HOST}" ] || { echo "无法解析端点" >&2; exit 1; }

MC_HOST_healthcheck="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@${HOST}"
export MC_HOST_healthcheck

exec /usr/bin/mc ls healthcheck >/dev/null 2>&1
