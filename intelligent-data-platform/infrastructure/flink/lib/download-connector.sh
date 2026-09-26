#!/usr/bin/env bash
# ============================================================
# 获取 Flink Kafka SQL Connector
# ============================================================
#
# 用法：
#   bash infrastructure/flink/lib/download-connector.sh
#
# 为什么用脚本下载而不是提交二进制到 Git：
#   该 JAR 约 5.4 MB，属第三方二进制产物，不适合进版本库。
#   因此仓库只保留本脚本，由使用者在构建镜像前执行一次。
#
# 版本查证（Maven Central maven-metadata.xml 实际返回）：
#   flink-sql-connector-kafka 的版本格式为 <connector>-<flink>：
#     3.3.0-1.19 / 3.3.0-1.20 / 3.4.0-1.20
#     4.0.0-2.0 / 5.0.0-2.x  -> 仅适用于 Flink 2.x
#   注意：**不存在 3.2.0-1.20**（只有 3.2.0-1.18 / 3.2.0-1.19）。
#   本项目使用 Flink 1.20.1，故取 3.4.0-1.20。
# ============================================================
set -euo pipefail

CONNECTOR_VER="3.4.0-1.20"
JAR_NAME="flink-sql-connector-kafka-${CONNECTOR_VER}.jar"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SCRIPT_DIR}/${JAR_NAME}"

MIRRORS=(
  "https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/${CONNECTOR_VER}/${JAR_NAME}"
  "https://maven.aliyun.com/repository/public/org/apache/flink/flink-sql-connector-kafka/${CONNECTOR_VER}/${JAR_NAME}"
)

if [ -s "${TARGET}" ]; then
  echo "已存在：${TARGET} ($(du -h "${TARGET}" | cut -f1))"
  exit 0
fi

for url in "${MIRRORS[@]}"; do
  echo ">>> 下载 ${url}"
  if curl -fsSL --max-time 300 -o "${TARGET}.part" "${url}"; then
    mv "${TARGET}.part" "${TARGET}"
    echo "    OK: $(du -h "${TARGET}" | cut -f1)"
    exit 0
  fi
  echo "    失败，尝试下一个源"
done

echo "ERROR: 所有下载源均失败" >&2
exit 1
