# ============================================================
# Spark 镜像（Sprint 2：离线链路）
# ============================================================
#
# 为什么在官方镜像上加一层，而不是 spark-submit --packages：
#   --packages 会在**每次提交时**联网解析并下载依赖，
#   服务器网络下既慢又容易失败；把 JAR 固化进镜像后，
#   作业启动不依赖外网，也让"同一镜像 = 同一运行环境"成立。
#
# 依赖版本对齐（关键）：
#   Spark 3.5.x 内置 hadoop-client 3.3.4，因此 hadoop-aws 必须用 3.3.4，
#   否则 S3A 会报 NoClassDefFoundError / NoSuchMethodError。
#   aws-java-sdk-bundle 用 1.12.262 是 hadoop-aws 3.3.4 的配套版本。
# ============================================================

FROM apache/spark:3.5.7

# 切到 root 复制依赖（官方镜像默认用户是 spark，uid 185）
USER root

# MySQL JDBC（读业务库）与 S3A（写 MinIO）
COPY lib/mysql-connector-j-8.4.0.jar /opt/spark/jars/
COPY lib/hadoop-aws-3.3.4.jar /opt/spark/jars/
COPY lib/aws-java-sdk-bundle-1.12.262.jar /opt/spark/jars/

# 结构化流读 Kafka 的连接器（Sprint 4 引入）
#
# !! 官方镜像**不带** Kafka 连接器，实测确认 !!
#   $ docker run --rm --entrypoint bash apache/spark:3.5.7 \
#         -c 'ls /opt/spark/jars/ | grep -i kafka'
#   （无输出）
#   缺了会报：AnalysisException: Failed to find data source: kafka.
#
# 四个 jar 缺一不可：
#   spark-sql-kafka-0-10              数据源实现
#   spark-token-provider-kafka-0-10   凭据委派（官方部署文档要求一起带上）
#   kafka-clients                     Kafka 客户端
#   commons-pool2                     kafka-clients 的运行时依赖
#
# 获取方式：infrastructure/spark/lib/download-connector.sh
#   （与 Sprint 1 给 Flink 补 Kafka 连接器同一思路：JAR 固化进镜像，
#     不在提交作业时用 --packages 联网解析）
#
# 版本对齐：两个 spark-* jar 严格等于 Spark 版本 3.5.7；
#   kafka-clients 3.4.1 是其配套版本。升级 Spark 时这几行都要复核。
COPY lib/spark-sql-kafka-0-10_2.12-3.5.7.jar /opt/spark/jars/
COPY lib/spark-token-provider-kafka-0-10_2.12-3.5.7.jar /opt/spark/jars/
COPY lib/kafka-clients-3.4.1.jar /opt/spark/jars/
COPY lib/commons-pool2-2.12.0.jar /opt/spark/jars/

# Iceberg 表格式（Sprint 5）
#
# !! 构件名里的 3.5 是 Spark 次版本、2.12 是 Scala 版本 !!
#   两者都必须与运行的 Spark 一致。写错时不会在构建期报错，
#   而是在运行期抛 NoSuchMethodError / ClassNotFoundException ——
#   又是一类"构建成功但跑起来才炸"的问题。
#
# iceberg-spark-runtime 是**打好的胖包**（含 Iceberg 依赖），
# 因此只需这一个 jar，不需要逐个补 iceberg-core 等。
#
# 获取方式：infrastructure/spark/lib/download-connector.sh
COPY lib/iceberg-spark-runtime-3.5_2.12-1.11.0.jar /opt/spark/jars/

# 项目统一的 Spark 默认配置（Metastore 地址、S3A、内存与并行度）
COPY conf/spark-defaults.conf /opt/spark/conf/spark-defaults.conf

# 作业脚本目录（由 compose 挂载覆盖，镜像内保留一份便于自检）
COPY jobs/ /opt/jobs/

# 复制进来的文件属主交给 spark，避免以非 root 运行时权限不足
RUN chown -R spark:spark /opt/spark/jars /opt/spark/conf/spark-defaults.conf /opt/jobs

USER spark

# 说明：不在镜像里写死 entrypoint ——
# master / worker / 提交作业三种用法由 docker-compose.yml 分别指定 command。
