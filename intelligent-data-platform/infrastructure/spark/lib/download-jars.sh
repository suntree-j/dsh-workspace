#!/usr/bin/env bash
# ============================================================
# 下载 Spark 侧依赖 JAR（MySQL JDBC + S3A）
# ============================================================
#
# 用法（仓库根目录，构建镜像前执行一次）：
#   bash infrastructure/spark/lib/download-jars.sh
#
# 为什么用脚本而不是把 JAR 提交进仓库：
#   三个 JAR 合计约 320 MB（aws-java-sdk-bundle 一个就 280 MB），
#   属于第三方二进制产物，不适合进版本库；本目录的 .gitignore 已排除 *.jar。
#
# 版本对齐说明见 infrastructure/spark/Dockerfile 顶部注释。
# ============================================================

set -euo pipefail

LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASE_URL="${MAVEN_BASE_URL:-https://repo1.maven.org/maven2}"

# 注意：MySQL 驱动的坐标在 8.0.31 之后从 mysql:mysql-connector-java
# 迁到了 com.mysql:mysql-connector-j（旧坐标已停止更新）。
declare -A JARS=(
  ["mysql-connector-j-8.4.0.jar"]="com/mysql/mysql-connector-j/8.4.0/mysql-connector-j-8.4.0.jar"
  ["hadoop-aws-3.3.4.jar"]="org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar"
  ["aws-java-sdk-bundle-1.12.262.jar"]="com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar"
)

echo "下载 Spark 依赖到 ${LIB_DIR}"
for name in "${!JARS[@]}"; do
    dest="${LIB_DIR}/${name}"
    if [ -s "${dest}" ]; then
        echo "  [跳过] ${name} 已存在（$(du -h "${dest}" | cut -f1)）"
        continue
    fi
    url="${BASE_URL}/${JARS[${name}]}"
    echo "  [下载] ${name}"
    if ! curl -fSL --retry 3 --retry-delay 2 --max-time 600 -o "${dest}.part" "${url}"; then
        echo "  [失败] ${url}" >&2
        rm -f "${dest}.part"
        exit 1
    fi
    mv "${dest}.part" "${dest}"
    echo "         $(du -h "${dest}" | cut -f1)"
done

echo
echo "完成，当前 lib 目录："
ls -lh "${LIB_DIR}"/*.jar
