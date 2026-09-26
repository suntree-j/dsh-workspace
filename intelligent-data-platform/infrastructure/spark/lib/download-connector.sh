#!/usr/bin/env bash
# ============================================================
# infrastructure/spark/lib/download-connector.sh
#   下载 Spark 缺失的 Kafka 连接器（Sprint 4 引入）
# ============================================================
#
# 用法（仓库根目录）：
#   bash infrastructure/spark/lib/download-connector.sh
#
# !! 为什么需要这个脚本 !!
#   apache/spark:3.5.7 官方镜像**不带 Kafka 连接器**（实测确认）：
#       $ ls /opt/spark/jars/ | grep -i kafka
#       （无输出）
#   镜像里只有 hadoop-aws / hive-jdbc / mysql-connector-j。
#   而结构化流读 Kafka 需要 `spark-sql-kafka-0-10`，否则报：
#       AnalysisException: Failed to find data source: kafka.
#         Please deploy the application as per the deployment section of
#         Structured Streaming + Kafka Integration Guide.
#
#   这与 Sprint 1 给 Flink 补 Kafka 连接器是同一类问题，
#   因此沿用同样的做法：**用脚本从 Maven Central 下载到 lib/，
#   再由 Dockerfile COPY 进镜像** —— 而不是用 spark-submit --packages
#   让容器在运行时联网解析（那样构建不可复现，且服务器访问
#   Maven Central 不稳定）。
#
# !! 四个 jar 缺一不可 !!
#   spark-sql-kafka-0-10   数据源实现
#   spark-token-provider-kafka-0-10
#                          凭据委派（部署文档明确要求一起带上）
#   kafka-clients          Kafka 客户端（版本必须与上面兼容）
#   commons-pool2          kafka-clients 的运行时依赖
#
# 版本策略：spark-* 两个 jar 严格等于 Spark 版本（3.5.7）。
#   kafka-clients / commons-pool2 若写死版本，会在 Spark 升级时变成
#   隐性不一致，所以这里显式列出并注明"升级 Spark 时须一并复核"。
# ============================================================

set -euo pipefail

SPARK_VERSION="3.5.7"
SCALA_BIN="2.12"

# kafka-clients：Spark 3.5.7 的 spark-sql-kafka 依赖 3.4.x 系列
KAFKA_CLIENTS_VERSION="3.4.1"
# commons-pool2：kafka-clients 的传递依赖
COMMONS_POOL2_VERSION="2.12.0"

# Iceberg：**必须选字节码目标不高于 Java 11 的版本**（Sprint 5 实测结论）
#
# !! 为什么不是最新的 1.11.0 !!
#   实测（读 jar 里 ExtendedParser.class 的 class file major version）：
#       1.11.0  → major 61 = Java 17   ❌
#       1.10.2  → major 55 = Java 11   ✅  ← 选它
#       1.10.0 / 1.9.2 / 1.8.1 / 1.7.2 → 均为 55 ✅
#   而 apache/spark:3.5.7 镜像里的 JRE 是 **Java 11**（Temurin 11.0.27）。
#   用 1.11.0 会抛：
#       UnsupportedClassVersionError: org/apache/iceberg/spark/ExtendedParser
#       has been compiled by a more recent version of the Java Runtime
#       (class file version 61.0), this version ... only recognizes ... up to 55.0
#
# !! 教训：不要用文档的"支持范围"代替字节码目标 !!
#   Iceberg 官方支持在 Java 11 上运行，但 1.11.0 的**发布字节码是 Java 17 目标**
#   —— "能在 Java 11 上跑" 与 "字节码要求 Java 17" 是两件事。
#   判定方法只有一种：**实测 class file major version**（本脚本注释上方列出了结果）。
#
# 何时可以升到 1.11+：当 Spark 镜像换成 Java 17 之后（届时需回归
# Sprint 2/3/4 的全部离线作业）。
ICEBERG_VERSION="1.10.2"

LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MAVEN_BASE="https://repo1.maven.org/maven2"

log() { printf '%s\n' "$*"; }

download() {
    local group_path="$1" artifact="$2" version="$3"
    local file="${artifact}-${version}.jar"
    local url="${MAVEN_BASE}/${group_path}/${artifact}/${version}/${file}"

    if [ -s "${LIB_DIR}/${file}" ]; then
        log "  已存在，跳过： ${file}"
        return 0
    fi

    log "  下载 ${file}"
    if ! curl -fSL --retry 3 --retry-delay 2 --max-time 180 -o "${LIB_DIR}/${file}.part" "${url}"; then
        log "  [FAIL] 下载失败： ${url}"
        rm -f "${LIB_DIR}/${file}.part"
        return 1
    fi
    mv "${LIB_DIR}/${file}.part" "${LIB_DIR}/${file}"
}

main() {
    log "====================================="
    log " 下载 Spark ${SPARK_VERSION} 的 Kafka 连接器"
    log "====================================="
    log "目标目录： ${LIB_DIR}"
    log ""

    local failed=0

    log "▶ Spark 侧（版本严格等于 Spark 版本）"
    download "org/apache/spark" "spark-sql-kafka-0-10_${SCALA_BIN}" "${SPARK_VERSION}" || failed=1
    download "org/apache/spark" "spark-token-provider-kafka-0-10_${SCALA_BIN}" "${SPARK_VERSION}" || failed=1

    log ""
    log "▶ Kafka 客户端与依赖"
    download "org/apache/kafka" "kafka-clients" "${KAFKA_CLIENTS_VERSION}" || failed=1
    download "org/apache/commons" "commons-pool2" "${COMMONS_POOL2_VERSION}" || failed=1

    log ""
    log "▶ Iceberg 表格式（Sprint 5）"
    log "  构件名里的 3.5 是 **Spark 次版本**、2.12 是 Scala 版本，"
    log "  两者都必须与运行的 Spark 一致，否则会出现"
    log "  NoSuchMethodError 这类运行期才暴露的错误。"
    download "org/apache/iceberg" "iceberg-spark-runtime-3.5_2.12" "${ICEBERG_VERSION}" || failed=1

    log ""
    if [ "${failed}" -ne 0 ]; then
        log "[FAIL] 有 jar 下载失败；检查网络后重跑（脚本是幂等的）"
        log "       若 Maven Central 不可达，可用镜像："
        log "       MAVEN_BASE=https://maven.aliyun.com/repository/public"
        exit 1
    fi

    log "已就绪："
    ls -lh "${LIB_DIR}"/*.jar 2>/dev/null | awk '{printf "  %-8s %s\n", $5, $9}'
    log ""
    log "下一步：重建 Spark 镜像，让 Dockerfile 把新 jar 拷进 /opt/spark/jars"
    log "  docker compose build spark-submit"
}

main "$@"
