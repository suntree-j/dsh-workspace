#!/usr/bin/env bash
# ============================================================
# Kafka Topic 初始化
# ============================================================
#
# 由 docker-compose.yml 中的 kafka-init 一次性容器执行。
# 该容器使用 Apache Kafka 官方镜像，因此可直接调用 kafka-topics.sh。
#
# 创建的 Topic（依据 docs/sprint/SPRINT_0.md 第 8 节）：
#   order_event     订单创建/状态变化
#   payment_event   支付成功/失败
#   refund_event    退款事件
#   behavior_event  浏览/点击/搜索/加购/收藏/购买行为
#
# 要求：partition = 3, replication factor = 1（单机开发环境不追求高可用）
# ============================================================

set -euo pipefail

BOOTSTRAP_SERVERS="${KAFKA_BOOTSTRAP_SERVERS:-kafka:9092}"
PARTITIONS="${KAFKA_TOPIC_PARTITIONS:-3}"
REPLICATION_FACTOR="${KAFKA_TOPIC_REPLICATION_FACTOR:-1}"

KAFKA_TOPICS_BIN="/opt/kafka/bin/kafka-topics.sh"

TOPICS=(
  "order_event"
  "payment_event"
  "refund_event"
  "behavior_event"
)

log()  { printf '[kafka-init] %s\n' "$*"; }
fail() { printf '[kafka-init] ERROR: %s\n' "$*" >&2; exit 1; }

if [ ! -x "${KAFKA_TOPICS_BIN}" ]; then
  fail "找不到 ${KAFKA_TOPICS_BIN}"
fi

# ------------------------------------------------------------
# 1. 等待 broker 真正就绪
#    容器已启动 != 服务可用，必须重试。
# ------------------------------------------------------------
log "等待 Kafka broker (${BOOTSTRAP_SERVERS}) 就绪 ..."
ready=0
for attempt in $(seq 1 30); do
  if "${KAFKA_TOPICS_BIN}" --bootstrap-server "${BOOTSTRAP_SERVERS}" --list >/dev/null 2>&1; then
    ready=1
    log "Kafka broker 已就绪（第 ${attempt} 次尝试）"
    break
  fi
  log "尚未就绪，重试 ${attempt}/30 ..."
  sleep 2
done

if [ "${ready}" -ne 1 ]; then
  fail "Kafka broker 在 60 秒内未就绪"
fi

# ------------------------------------------------------------
# 2. 幂等创建 Topic
# ------------------------------------------------------------
existing="$("${KAFKA_TOPICS_BIN}" --bootstrap-server "${BOOTSTRAP_SERVERS}" --list 2>/dev/null || true)"

for topic in "${TOPICS[@]}"; do
  if printf '%s\n' "${existing}" | grep -qx "${topic}"; then
    log "Topic 已存在，跳过：${topic}"
    continue
  fi

  log "创建 Topic：${topic} (partitions=${PARTITIONS}, replication-factor=${REPLICATION_FACTOR})"
  "${KAFKA_TOPICS_BIN}" \
    --bootstrap-server "${BOOTSTRAP_SERVERS}" \
    --create \
    --topic "${topic}" \
    --partitions "${PARTITIONS}" \
    --replication-factor "${REPLICATION_FACTOR}" \
    || fail "创建 Topic 失败：${topic}"
done

# ------------------------------------------------------------
# 3. 校验结果
# ------------------------------------------------------------
log "当前 Topic 列表："
"${KAFKA_TOPICS_BIN}" --bootstrap-server "${BOOTSTRAP_SERVERS}" --list

missing=0
for topic in "${TOPICS[@]}"; do
  if ! "${KAFKA_TOPICS_BIN}" --bootstrap-server "${BOOTSTRAP_SERVERS}" --describe --topic "${topic}" >/dev/null 2>&1; then
    log "缺失 Topic：${topic}"
    missing=1
  fi
done

if [ "${missing}" -ne 0 ]; then
  fail "有 Topic 未创建成功"
fi

log "全部 ${#TOPICS[@]} 个 Topic 创建完成"
