#!/bin/sh
# ============================================================
# MinIO Bucket 初始化
# ============================================================
#
# 由 docker-compose.yml 中的 minio-init 一次性容器执行。
#
# 该容器复用 minio 镜像内置的 /usr/bin/mc。
# 原因：MinIO 自 2025-10 起停止分发官方镜像，minio/mc 已从
#       Docker Hub 下架（404），因此不引入额外镜像。
# 详见 docs/development-environment.md 第 4.3 节。
#
# !! 重要：运行环境限制 !!
#   minio 镜像基于 BusyBox，**不含 sed / awk / grep / curl / wget / tar**，
#   只有 sh 内建、busybox 基础工具与 mc。
#   因此本脚本：
#     - 只用 POSIX 参数展开处理字符串（不用 sed）
#     - 不使用 grep，改用 case / mc 自身的退出码
#   详见 docs/sprint/SPRINT_0_VERIFICATION_STATUS.md 的实测记录。
#
# 创建 Bucket：lakehouse
# 后续规划（Sprint 0 只创建 bucket，不创建前缀目录、不接入 Iceberg）：
#   lakehouse/
#   ├── warehouse/
#   ├── metadata/
#   ├── checkpoint/
#   └── archive/
# ============================================================

set -eu

MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
MINIO_BUCKET="${MINIO_BUCKET:-lakehouse}"
MINIO_ALIAS="local"

MC_BIN="/usr/bin/mc"

log()  { printf '[minio-init] %s\n' "$*"; }
fail() { printf '[minio-init] ERROR: %s\n' "$*" >&2; exit 1; }

[ -x "${MC_BIN}" ] || fail "找不到 ${MC_BIN}"

# ------------------------------------------------------------
# 1. 用 POSIX 参数展开剥离协议前缀
#    （镜像内没有 sed；${var#pattern} 是 shell 内建，始终可用）
# ------------------------------------------------------------
HOST=${MINIO_ENDPOINT#http://}
HOST=${HOST#https://}
[ -n "${HOST}" ] || fail "无法从 MINIO_ENDPOINT 解析主机：${MINIO_ENDPOINT}"

# 通过 MC_HOST_<alias> 注入凭据，避免出现在进程参数中
MC_HOST_local="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@${HOST}"
export MC_HOST_local

log "端点：${HOST}（别名 ${MINIO_ALIAS}）"

# ------------------------------------------------------------
# 2. 等待 MinIO 真正就绪
#
#    注意：`mc ready <url>` 对未配置别名的地址会返回
#    "Couldn't construct anonymous client" 且退出码为 0，
#    不能用于判定。这里改用 `mc ls <alias>`：
#    只有服务真正可用时才会成功。
# ------------------------------------------------------------
log "等待 MinIO 就绪 ..."
ready=0
attempt=1
while [ "${attempt}" -le 30 ]; do
  if "${MC_BIN}" ls "${MINIO_ALIAS}" >/dev/null 2>&1; then
    ready=1
    log "MinIO 已就绪（第 ${attempt} 次尝试）"
    break
  fi
  log "尚未就绪，重试 ${attempt}/30 ..."
  sleep 2
  attempt=$((attempt + 1))
done

[ "${ready}" -eq 1 ] || fail "MinIO 在 60 秒内未就绪，请检查 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD"

# ------------------------------------------------------------
# 3. 幂等创建 Bucket
# ------------------------------------------------------------
if "${MC_BIN}" ls "${MINIO_ALIAS}/${MINIO_BUCKET}" >/dev/null 2>&1; then
  log "Bucket 已存在，跳过：${MINIO_BUCKET}"
else
  log "创建 Bucket：${MINIO_BUCKET}"
  "${MC_BIN}" mb --ignore-existing "${MINIO_ALIAS}/${MINIO_BUCKET}" \
    || fail "创建 Bucket 失败：${MINIO_BUCKET}"
fi

# ------------------------------------------------------------
# 4. 校验结果（用 mc 退出码，不用 grep）
# ------------------------------------------------------------
"${MC_BIN}" ls "${MINIO_ALIAS}/${MINIO_BUCKET}" >/dev/null 2>&1 \
  || fail "Bucket 校验失败：${MINIO_BUCKET}"

log "Bucket 列表："
"${MC_BIN}" ls "${MINIO_ALIAS}"

log "Bucket ${MINIO_BUCKET} 创建完成"
