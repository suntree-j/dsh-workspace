#!/usr/bin/env bash
# ============================================================
# scripts/init-lakehouse.sh — 初始化离线链路（Sprint 2，幂等）
# ============================================================
#
# 用法（服务器上，仓库根目录）：
#   bash scripts/init-lakehouse.sh
#
# 做什么：
#   1. 内存降配自检（确认 .env 已按 SPRINT_2.md 2.3 节调整）
#   2. 在 MySQL 里建 Hive 元数据库与专用账号（口令从 .env 注入）
#   3. 下载 Spark 依赖 JAR 并构建 Spark 镜像
#   4. 启动 hive-metastore / spark-master / spark-worker
#   5. 用官方 schematool 初始化 Metastore 表结构
#   6. 自检：Spark 集群有 Worker 注册、Metastore 能列出库
#
# 为什么 Metastore 表结构必须用 schematool：
#   元数据表有版本号，手工建表会导致后续升级/兼容问题；
#   官方工具保证与 Hive 版本严格匹配。
# ============================================================

# shellcheck source=lib/common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib/common.sh"

SPARK_DIR="${REPO_ROOT}/infrastructure/spark"
FAILED=0

log_step() {
    printf '\n'
    printf '%b\n' "${C_BOLD}─────────────────────────────────────────${C_RESET}"
    printf '%b\n' "${C_BOLD}▶ $*${C_RESET}"
    printf '%b\n' "${C_BOLD}─────────────────────────────────────────${C_RESET}"
}

compose() {
    docker compose -f "${REPO_ROOT}/docker-compose.yml" --project-directory "${REPO_ROOT}" "$@"
}

# ------------------------------------------------------------
# 1. 内存自检
# ------------------------------------------------------------
check_memory() {
    log_step "1/6 内存自检"

    local available
    available="$(free -m | awk '/^Mem:/ {print $7}')"
    printf '  当前可用内存：%s MB\n' "${available}"

    local tm jm
    tm="$(grep -E '^FLINK_TASKMANAGER_MEMORY=' "${REPO_ROOT}/.env" | cut -d= -f2- || true)"
    jm="$(grep -E '^FLINK_JOBMANAGER_MEMORY=' "${REPO_ROOT}/.env" | cut -d= -f2- || true)"

    if [ -z "${tm}" ] || [ -z "${jm}" ]; then
        log_warn "未在 .env 中设置 FLINK_TASKMANAGER_MEMORY / FLINK_JOBMANAGER_MEMORY"
        log_warn "Sprint 2 需要降配才能容纳 Spark + Hive，请参照 .env.example 与 SPRINT_2.md 2.3 节"
        FAILED=1
        return
    fi
    log_ok "Flink 内存：TaskManager=${tm}  JobManager=${jm}"

    if [ "${available}" -lt 1200 ]; then
        log_warn "可用内存偏低（${available} MB）。Spark 作业以 1 GB 驱动 + 1 GB 执行器运行，"
        log_warn "建议先执行 bash scripts/stop.sh 停掉不必要的服务，或确认降配已生效。"
    else
        log_ok "可用内存满足 Spark + Hive 运行要求"
    fi
}

# ------------------------------------------------------------
# 2. MySQL：元数据库与账号
# ------------------------------------------------------------
create_metastore_db() {
    log_step "2/6 创建 Hive 元数据库与账号"

    local user password
    user="$(grep -E '^HIVE_METASTORE_USER=' "${REPO_ROOT}/.env" | cut -d= -f2- || echo hive)"
    password="$(grep -E '^HIVE_METASTORE_PASSWORD=' "${REPO_ROOT}/.env" | cut -d= -f2- || true)"

    if [ -z "${password}" ] || [ "${password}" = "change_me_hive_metastore" ]; then
        log_error "请在 .env 中设置 HIVE_METASTORE_PASSWORD（不要使用占位值）"
        FAILED=1
        return
    fi

    # 口令通过 stdin 交给 mysql，不出现在命令行（/proc/<pid>/cmdline 全局可读）
    local sql_file
    sql_file="$(mktemp)"
    chmod 600 "${sql_file}"
    {
        printf "CREATE DATABASE IF NOT EXISTS hive_metastore CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
        printf "CREATE USER IF NOT EXISTS '%s'@'%%' IDENTIFIED BY '%s';\n" "${user}" "${password}"
        printf "ALTER USER '%s'@'%%' IDENTIFIED BY '%s';\n" "${user}" "${password}"
        printf "GRANT ALL PRIVILEGES ON hive_metastore.* TO '%s'@'%%';\n" "${user}"
        printf "FLUSH PRIVILEGES;\n"
    } > "${sql_file}"

    local out
    if out="$(docker exec -i mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD"' < "${sql_file}" 2>&1)"; then
        log_ok "元数据库 hive_metastore 与账号 ${user} 已就绪"
    else
        log_error "创建元数据库失败：${out}"
        FAILED=1
    fi
    rm -f "${sql_file}"
}

# ------------------------------------------------------------
# 3. Spark 依赖与镜像
# ------------------------------------------------------------
build_spark_image() {
    log_step "3/6 准备 Spark 依赖并构建镜像"

    if ! bash "${SPARK_DIR}/lib/download-jars.sh" >/tmp/spark-jars.log 2>&1; then
        log_error "依赖下载失败，详见 /tmp/spark-jars.log"
        FAILED=1
        return
    fi
    grep -E '\[下载\]|\[跳过\]' /tmp/spark-jars.log | sed 's/^/  /' || true

    if compose build spark-master >/tmp/spark-build.log 2>&1; then
        log_ok "Spark 镜像构建完成：$(docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep data-platform/spark | head -1)"
    else
        log_error "镜像构建失败，详见 /tmp/spark-build.log"
        tail -20 /tmp/spark-build.log | sed 's/^/  /'
        FAILED=1
    fi
}

# ------------------------------------------------------------
# 3.5 Metastore 侧依赖（从 Metastore 镜像里提取，版本严格对齐）
#
# 为什么需要：
#   Metastore 服务端在解析 s3a:// 的库/表位置时要加载 S3AFileSystem，
#   而镜像把 hadoop-aws 放在 Hadoop 的 tools/lib 下、不在 Hive 的 classpath 上，
#   于是建库/建表时报
#     ClassNotFoundException: Class org.apache.hadoop.fs.s3a.S3AFileSystem not found
#   提取时必须用**镜像自带那份**（Hive 3.1.3 镜像 = Hadoop 3.1.0，
#   配 Spark 侧的 hadoop-aws 3.3.4 会 NoSuchMethodError）。
# ------------------------------------------------------------
prepare_metastore_jars() {
    log_step "3.5/6 准备 Metastore 依赖（S3A，版本对齐镜像）"

    local client_dir="${REPO_ROOT}/infrastructure/hive/client-lib"
    local hive_version
    hive_version="$(grep -E '^HIVE_VERSION=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2- || echo 3.1.3)"
    local image="apache/hive:${hive_version}"
    mkdir -p "${client_dir}"

    if [ -s "${client_dir}/hadoop-aws-3.1.0.jar" ] \
       && [ -s "${client_dir}/aws-java-sdk-bundle-1.11.271.jar" ]; then
        log_ok "S3A 依赖已存在，跳过提取"
    else
        log_info "从 ${image} 提取 S3A 依赖 ..."
        if docker run --rm --user root --entrypoint bash -v "${client_dir}:/out" "${image}" -c '
                cp -n /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-*.jar /out/ 2>/dev/null
                cp -n /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-*.jar /out/ 2>/dev/null
                ls /out | tr "\n" " "
            ' >/tmp/metastore-jars.log 2>&1; then
            log_ok "已提取：$(cat /tmp/metastore-jars.log | tail -1)"
        else
            log_error "提取失败，详见 /tmp/metastore-jars.log"
            tail -5 /tmp/metastore-jars.log | sed 's/^/  /'
            FAILED=1
        fi
    fi
}

# ------------------------------------------------------------
# 4. 启动服务
# ------------------------------------------------------------
start_services() {
    log_step "4/6 启动 hive-metastore / spark-master / spark-worker"

    # 4.1 先修正 spark-work 卷的属主
    #     命名的 Docker 卷默认属主是 root，而 Spark 镜像以 uid 185(spark) 运行，
    #     不修的话执行器起不来，报：
    #       Failed to create directory /opt/spark/work/app-xxx/0
    #     进而拖垮整个作业（驱动侧只看到 MetricsSystem 之类的次生错误，
    #     真正的根因在 worker 日志里）。
    local work_volume="data-platform-spark-work"
    if docker volume inspect "${work_volume}" >/dev/null 2>&1; then
        docker run --rm -v "${work_volume}:/w" alpine:3.20 chown -R 185:185 /w >/dev/null 2>&1 || true
        log_info "已修正 ${work_volume} 的属主（spark=185）"
    fi

    if ! compose up -d hive-metastore spark-master spark-worker 2>&1 | tail -6; then
        log_error "服务启动失败"
        FAILED=1
        return
    fi

    local deadline=$(( SECONDS + 180 ))
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        local ok=0
        for c in hive-metastore spark-master spark-worker; do
            [ "$(docker inspect -f '{{.State.Health.Status}}' "${c}" 2>/dev/null)" = "healthy" ] && ok=$(( ok + 1 ))
        done
        if [ "${ok}" -eq 3 ]; then
            log_ok "三个容器均已 healthy"
            return
        fi
        printf '\r%b' "${C_BLUE}[INFO]${C_RESET} 等待就绪 ... ${ok}/3 健康 (${SECONDS}s)"
        sleep 5
    done
    printf '\r\033[K'
    log_error "等待超时，请检查： docker compose logs --tail=50 hive-metastore spark-master spark-worker"
    FAILED=1
}

# ------------------------------------------------------------
# 4.5 湖仓目录（S3 前缀）
#
# 为什么必须显式创建：
#   S3 没有真正的目录。Spark 在启动时会检查 eventLog 目录是不是"目录"
#   （EventLogFileWriter.requireLogBaseDirAsDirectory），空前缀不存在
#   会让 spark-sql **启动直接失败**（实测：Unable to load ... / not a directory）。
#   用 mc mb 建出带斜杠的零字节对象，S3A 就会把它识别为目录。
# ------------------------------------------------------------
prepare_s3_dirs() {
    log_step "4.5/6 创建湖仓目录（MinIO 前缀）"

    local user password
    user="$(grep -E '^MINIO_ROOT_USER=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    password="$(grep -E '^MINIO_ROOT_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    local bucket
    bucket="$(grep -E '^MINIO_BUCKET=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2- || echo lakehouse)"

    local out
    if out="$(docker exec minio sh -c "
        MC_HOST_local='http://${user}:${password}@localhost:9000'; export MC_HOST_local
        for d in '${bucket}/warehouse' '${bucket}/warehouse/ods' '${bucket}/spark-events'; do
            mc mb --ignore-existing \"local/\$d\" >/dev/null 2>&1 || true
        done
        mc ls \"local/${bucket}\"
    " 2>&1)"; then
        printf '%s\n' "${out}" | sed 's/^/  /'
        log_ok "湖仓目录已就绪（warehouse / warehouse/ods / spark-events）"
    else
        log_error "创建湖仓目录失败：${out}"
        FAILED=1
    fi
}

# ------------------------------------------------------------
# 5. Metastore schema（必须在启动 Metastore 之前完成）
#
# 为什么提前做：
#   本项目的 Metastore 设了 IS_RESUME=true（见 docker-compose.yml 注释），
#   即 entrypoint 不再自动初始化 schema。原因是 Hive 3.1.x 的 entrypoint
#   每次启动都跑 `-initSchema`，表已存在时报
#     Error: Table 'CTLGS' already exists → 容器崩溃重启。
#   因此改由这里用**一次性容器**初始化一次，之后每次启动都跳过。
# ------------------------------------------------------------
init_metastore_schema() {
    log_step "5/6 初始化 Hive Metastore 表结构（schematool，一次性容器）"

    local hive_version user password
    hive_version="$(grep -E '^HIVE_VERSION=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2- || echo 3.1.3)"
    user="$(grep -E '^HIVE_METASTORE_USER=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2- || echo hive)"
    password="$(grep -E '^HIVE_METASTORE_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
    local image="apache/hive:${hive_version}"
    local bucket
    bucket="$(grep -E '^MINIO_BUCKET=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2- || echo lakehouse)"

    # 检查是否已初始化（-info 成功即视为就绪）
    #
    # 注意 `--entrypoint /opt/hive/bin/schematool`：
    #   apache/hive 镜像的 ENTRYPOINT 是 `sh -c /entrypoint.sh`，它会**忽略**
    #   我们追加的命令行参数，改用环境变量 DB_DRIVER（默认 derby）自己跑一遍。
    #   实测后果是它拿了 Derby 脚本去初始化 MySQL：
    #     Initialization script hive-schema-3.1.0.derby.sql
    #     Error: You have an error in your SQL syntax near '"APP"."NUCLEUS_ASCII"'
    #   因此这里必须绕过 entrypoint 直接调 schematool。
    local run_args=(
        --rm --network data-platform
        --entrypoint /opt/hive/bin/schematool
        -v "${REPO_ROOT}/infrastructure/hive/conf/hive-site.xml:/opt/hive/conf/hive-site.xml:ro"
        -v "${REPO_ROOT}/infrastructure/spark/lib/mysql-connector-j-8.4.0.jar:/opt/hive/lib/mysql-connector-j-8.4.0.jar:ro"
        -e "HIVE_METASTORE_USER=${user}"
        -e "HIVE_METASTORE_PASSWORD=${password}"
        -e "S3_ENDPOINT=http://minio:9000"
        -e "S3_ACCESS_KEY=$(grep -E '^MINIO_ROOT_USER=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
        -e "S3_SECRET_KEY=$(grep -E '^MINIO_ROOT_PASSWORD=' "${REPO_ROOT}/.env" | head -1 | cut -d= -f2-)"
        "${image}"
    )

    if docker run "${run_args[@]}" -dbType mysql -info >/dev/null 2>&1; then
        log_ok "元数据表结构已就绪，跳过初始化"
        return
    fi

    log_info "初始化表结构 ..."
    local out
    if out="$(docker run "${run_args[@]}" -dbType mysql -initSchema 2>&1)"; then
        printf '%s\n' "${out}" | tail -3 | sed 's/^/  /'
        log_ok "Metastore 表结构初始化完成（bucket=${bucket}）"
    else
        log_error "初始化失败："
        printf '%s\n' "${out}" | tail -15 | sed 's/^/  /'
        log_error "常见原因：MySQL 账号权限不足 / hive-site.xml 里的 \${env:VAR} 未取到值"
        FAILED=1
    fi
}

# ------------------------------------------------------------
# 6. 自检
# ------------------------------------------------------------
self_check() {
    log_step "6/6 自检"

    # 6.1 Spark 集群：Worker 已注册
    #     注意用**服务名**探测：Spark 的 MasterUI 绑定在容器主机名对应的地址上，
    #     用 localhost 会 Connection refused（实测踩过，连健康检查都过不去）。
    #
    #     另外：这里每个取值都显式 `|| true`。脚本是 `set -e` 模式，
    #     命令替换里的 grep 一旦没匹配到就会让整个脚本静默退出
    #     —— 第一次写这段时正是这样"自检没有任何输出就结束了"。
    local master_json
    master_json="$(docker exec spark-master curl -fsS http://spark-master:8080/json/ 2>/dev/null || true)"
    if printf '%s' "${master_json}" | grep -q '"aliveworkers"'; then
        # Spark 的 /json/ 是"美化输出"（冒号两侧带空格），因此先把空格与换行去掉
        # 再匹配，否则 grep -o '...":[0-9]*' 一个都匹配不到（实测踩过）。
        local compact workers cores memory
        compact="$(printf '%s' "${master_json}" | tr -d ' \n' || true)"
        workers="$(printf '%s' "${compact}" | grep -o '"aliveworkers":[0-9]*' | cut -d: -f2 || true)"
        cores="$(printf '%s' "${compact}" | grep -o '"cores":[0-9]*' | head -1 | cut -d: -f2 || true)"
        memory="$(printf '%s' "${compact}" | grep -o '"memory":[0-9]*' | head -1 | cut -d: -f2 || true)"
        # 注意：Spark 的 /json/ 里 memory 单位**已经是 MB**，不要再除 1024
        log_ok "Spark 集群：Worker ${workers:-?} 个，可用 core ${cores:-?}，可用内存 ${memory:-?} MB"
    else
        log_error "Spark Master UI 无响应（用服务名 http://spark-master:8080/json/ 探测）"
        FAILED=1
    fi

    # 6.2 Metastore 通路：用 scripts/spark-sql.sh（一次性容器 + 注入 S3A 凭据）
    #     为什么要走脚本而不是直接 docker run：
    #       Hive 客户端在启动时会校验 warehouse 目录（s3a://lakehouse/warehouse），
    #       即使只执行 SHOW DATABASES 也需要 S3A 凭据，否则报
    #       Unable to load AWS credentials from environment variables。
    local dbs
    dbs="$(bash "${REPO_ROOT}/scripts/spark-sql.sh" -e "SHOW DATABASES;" 2>/dev/null \
        | grep -vE '^(Time taken|[[:space:]]*$)' || true)"

    if printf '%s' "${dbs}" | grep -q "default"; then
        log_ok "Spark SQL 可以访问 Hive Metastore（库列表：$(printf '%s' "${dbs}" | tr '\n' ' '))"
    else
        log_error "Spark SQL 无法访问 Metastore，请检查 hive-metastore 日志与 thrift 端口"
        FAILED=1
    fi

    printf '\n'
    if [ "${FAILED}" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}${C_BOLD} 离线链路初始化完成${C_RESET}"
        printf '\n'
        printf '  下一步：\n'
        printf '    bash scripts/submit-offline-job.sh     # 抽取 MySQL → 湖仓 ODS\n'
        printf '    bash scripts/verify-sprint-2.sh        # 验收\n'
        printf '    Spark UI: http://<宿主IP>:%s/\n' "$(grep -E '^SPARK_MASTER_WEBUI_PORT=' "${REPO_ROOT}/.env" | cut -d= -f2- || echo 8080)"
        return 0
    fi
    printf '%b\n' "${C_RED}${C_BOLD} 初始化未通过，请按上方提示排查${C_RESET}"
    return 1
}

main() {
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"
    printf '%b\n' "${C_BOLD} 初始化离线链路（Sprint 2）${C_RESET}"
    printf '%b\n' "${C_BOLD}=====================================${C_RESET}"

    load_env
    require_docker

    check_memory
    create_metastore_db
    build_spark_image
    prepare_metastore_jars
    init_metastore_schema
    start_services
    prepare_s3_dirs
    self_check

    exit $?
}

main "$@" < /dev/null
