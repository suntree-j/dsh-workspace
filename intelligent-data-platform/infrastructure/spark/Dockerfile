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

# 项目统一的 Spark 默认配置（Metastore 地址、S3A、内存与并行度）
COPY conf/spark-defaults.conf /opt/spark/conf/spark-defaults.conf

# 作业脚本目录（由 compose 挂载覆盖，镜像内保留一份便于自检）
COPY jobs/ /opt/jobs/

# 复制进来的文件属主交给 spark，避免以非 root 运行时权限不足
RUN chown -R spark:spark /opt/spark/jars /opt/spark/conf/spark-defaults.conf /opt/jobs

USER spark

# 说明：不在镜像里写死 entrypoint ——
# master / worker / 提交作业三种用法由 docker-compose.yml 分别指定 command。
