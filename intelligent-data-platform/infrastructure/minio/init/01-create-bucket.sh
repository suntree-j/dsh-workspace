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

if [ ! -x "${MC_BIN}" ]; then
  fail "找不到 ${MC_BIN}"
fi

# ------------------------------------------------------------
# 1. 等待 MinIO 真正就绪
# ------------------------------------------------------------
log "等待 MinIO (${MINIO_ENDPOINT}) 就绪 ..."
ready=0
for attempt in $(seq 1 30); do
  if "${MC_BIN}" ready "${MINIO_ENDPOINT}" >/dev/null 2>&1; then
    ready=1
    log "MinIO 已就绪（第 ${attempt} 次尝试）"
    break
  fi
  log "尚未就绪，重试 ${attempt}/30 ..."
  sleep 2
done

if [ "${ready}" -ne 1 ]; then
  fail "MinIO 在 60 秒内未就绪"
fi

# ------------------------------------------------------------
# 2. 配置别名
# ------------------------------------------------------------
# 注意：本脚本运行在 minio 镜像的 BusyBox sh 中，
#       不支持 ${var/pat/rep} 与 export NAME_${x}= 语法，
#       因此使用 sed 处理协议前缀，并用 eval 做间接变量赋值。
# 凭据通过环境变量传递，不出现在进程参数中。
HOST_AND_CREDENTIALS="$(printf '%s' "${MINIO_ENDPOINT}" | sed -e 's|^https://||' -e 's|^http://||')"
MC_HOST_VALUE="${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@${HOST_AND_CREDENTIALS}"

log "校验连接 ..."
eval "export MC_HOST_${MINIO_ALIAS}=\"\${MC_HOST_VALUE}\""

"${MC_BIN}" admin info "${MINIO_ALIAS}" >/dev/null 2>&1 || \
  fail "无法连接 MinIO，请检查 MINIO_ROOT_USER / MINIO_ROOT_PASSWORD"

# ------------------------------------------------------------
# 3. 幂等创建 Bucket
# ------------------------------------------------------------
if "${MC_BIN}" ls "${MINIO_ALIAS}/${MINIO_BUCKET}" >/dev/null 2>&1; then
  log "Bucket 已存在，跳过：${MINIO_BUCKET}"
else
  log "创建 Bucket：${MINIO_BUCKET}"
  "${MC_BIN}" mb "${MINIO_ALIAS}/${MINIO_BUCKET}" || fail "创建 Bucket 失败：${MINIO_BUCKET}"
fi

# ------------------------------------------------------------
# 4. 校验结果
# ------------------------------------------------------------
if ! "${MC_BIN}" ls "${MINIO_ALIAS}" | grep -q "${MINIO_BUCKET}"; then
  fail "Bucket 校验失败：${MINIO_BUCKET}"
fi

log "Bucket 列表："
"${MC_BIN}" ls "${MINIO_ALIAS}"

log "Bucket ${MINIO_BUCKET} 创建完成"
