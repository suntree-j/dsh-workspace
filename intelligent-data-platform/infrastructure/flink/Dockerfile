# ============================================================
# Flink 镜像（内置 Kafka SQL Connector）
# ============================================================
#
# 为什么需要这个 Dockerfile：
#   Flink 只从 $FLINK_HOME/lib 加载 connector。挂载宿主目录到 lib
#   会覆盖镜像自带的 Flink 发行版 JAR，因此必须在构建期把 connector
#   的 JAR **合并**进 lib 目录。
#
# connector 版本：flink-sql-connector-kafka 3.4.0-1.20
#   - 版本格式为 <connector>-<flink>，必须与 Flink 1.20.x 匹配
#   - 该 JAR 为 "sql" 版，已包含全部依赖，直接放 lib/ 即可
#   - 获取方式见 infrastructure/flink/lib/README.md
# ============================================================
FROM flink:1.20.1-scala_2.12-java17

# 时区与项目统一
ENV TZ=Asia/Shanghai

# 合并 Kafka SQL connector（不覆盖原有 lib 内容）
COPY lib/flink-sql-connector-kafka-3.4.0-1.20.jar /opt/flink/lib/

# 校验 JAR 确实就位（构建期失败好过运行期才发现）
RUN ls -l /opt/flink/lib/flink-sql-connector-kafka-*.jar
