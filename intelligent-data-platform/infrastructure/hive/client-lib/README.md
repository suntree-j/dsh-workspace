# Hive Metastore 客户端 JAR（从 apache/hive 镜像中一次性提取，约 400 MB）

这些 JAR 用于让 **Spark 使用与 Metastore 服务端完全一致的 Hive 客户端版本**
（`spark.sql.hive.metastore.jars=path`）。

为什么必须这样做：
    Spark 3.5 内置的 Hive 客户端是 **2.3.9**，而 Metastore 服务端是 **4.0.1**；
    Hive 4 移除了旧的 thrift 方法 `get_table`，于是 Spark 建表时报
      HiveException: Unable to fetch table ods_user. Invalid method name: 'get_table'
    换成 Hive 3.1.3 服务端（保留旧方法）也可行，但镜像拉取极慢；
    直接把同版本客户端 JAR 挂给 Spark 更确定，也不需要联网。

提取方式见 scripts/init-lakehouse.sh 的 `prepare_hive_client_jars()`。

第三方二进制，不入 Git。
