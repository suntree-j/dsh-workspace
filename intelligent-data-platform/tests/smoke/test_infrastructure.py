"""Sprint 0 基础设施冒烟测试。

前置条件：
    cp .env.example .env
    docker compose up -d
    （等待 Doris 首次启动完成）

运行：
    python -m pytest -m smoke -v

对应 SPRINT_0.md 第 19 节要求的 10 项验证：
    1. MySQL 可以连接
    2. ecommerce 数据库存在
    3. 业务表存在
    4. Kafka Topic 存在
    5. Kafka 可以生产消息
    6. Kafka 可以消费消息
    7. MinIO Bucket 存在
    8. Doris 可以连接
    9. Doris 可以建表
   10. Doris 可以查询测试数据

说明：
    测试通过 `docker exec` 在容器内部执行，
    因此不要求宿主机安装 mysql / kafka 客户端，
    也不依赖宿主端口映射，容器间统一使用服务名。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parents[2]

# ------------------------------------------------------------
# .env 读取
# ------------------------------------------------------------
def _read_env() -> dict[str, str]:
    """读取仓库根目录 .env（键值对解析，不做 shell 求值）。"""
    env: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return env
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


_ENV = _read_env()


def cfg(name: str, default: str) -> str:
    """优先取环境变量（支持容器内运行），其次 .env，最后默认值。"""
    return os.environ.get(name) or _ENV.get(name) or default


MYSQL_ROOT_PASSWORD = cfg("MYSQL_ROOT_PASSWORD", "")
MYSQL_DATABASE = cfg("MYSQL_DATABASE", "ecommerce")
MYSQL_USER = cfg("MYSQL_USER", "app")
MYSQL_PASSWORD = cfg("MYSQL_PASSWORD", "")

MINIO_ROOT_USER = cfg("MINIO_ROOT_USER", "")
MINIO_ROOT_PASSWORD = cfg("MINIO_ROOT_PASSWORD", "")
MINIO_BUCKET = cfg("MINIO_BUCKET", "lakehouse")

DORIS_FE_IP = cfg("DORIS_FE_IP", "172.28.0.10")
DORIS_BE_IP = cfg("DORIS_BE_IP", "172.28.0.11")

KAFKA_TOPICS = ["order_event", "payment_event", "refund_event", "behavior_event"]
MYSQL_TABLES = ["user", "product", "orders", "payment", "refund"]


# ------------------------------------------------------------
# 容器执行辅助
# ------------------------------------------------------------
def _resolve_docker() -> str | None:
    """定位 docker 可执行文件。

    Windows 上 Docker Desktop 安装后可能尚未加入当前 shell 的 PATH，
    因此额外探测默认安装位置，避免 pytest 因找不到命令而误报跳过。
    """
    import shutil

    found = shutil.which("docker")
    if found:
        return found

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        Path("/usr/bin/docker"),
        Path("/usr/local/bin/docker"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


DOCKER = _resolve_docker()


def docker_exec(container: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """在容器内执行命令。"""
    assert DOCKER, "未找到 docker 可执行文件"
    cmd = [DOCKER, "exec", container, *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False, errors="replace"
    )


def docker_exec_out(container: str, *args: str, timeout: int = 60) -> str:
    """在容器内执行命令并返回 stdout（失败时抛断言）。"""
    result = docker_exec(container, *args, timeout=timeout)
    assert result.returncode == 0, (
        f"docker exec {container} {' '.join(args)} 失败\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return result.stdout


def container_running(name: str) -> bool:
    assert DOCKER, "未找到 docker 可执行文件"
    result = subprocess.run(
        [DOCKER, "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() == "true"


# ------------------------------------------------------------
# 会话级前置检查
# ------------------------------------------------------------
CORE_CONTAINERS = ("mysql", "kafka", "minio")
DORIS_CONTAINERS = ("doris-fe", "doris-be")


@pytest.fixture(scope="session", autouse=True)
def require_infrastructure() -> None:
    """核心服务前置条件：Docker 可用且 MySQL / Kafka / MinIO 在运行。

    设计说明：Doris 首次启动需要初始化元数据、注册 BE，耗时较长，
    因此**不作为全部冒烟测试的前置条件**；Doris 相关用例自行判断
    （见 require_doris），这样在 Doris 尚未就绪时仍能验证其余组件。
    """
    if not DOCKER:
        pytest.skip(
            "未找到 docker 可执行文件。\n"
            "  请安装并启动 Docker Desktop，或将其加入 PATH：\n"
            r"  C:\Program Files\Docker\Docker\resources\bin"
        )

    try:
        probe = subprocess.run(
            [DOCKER, "info"], capture_output=True, text=True, timeout=30, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"docker 不可用：{exc}")

    if probe.returncode != 0:
        pytest.skip(
            "Docker daemon 未运行。\n"
            "  请启动 Docker Desktop 并等待其显示 Engine running，"
            "然后执行 docker compose up -d"
        )

    missing = [name for name in CORE_CONTAINERS if not container_running(name)]
    if missing:
        pytest.skip(
            f"以下核心容器未运行：{', '.join(missing)}。\n"
            "  请先执行： bash scripts/start.sh  （或 docker compose up -d）"
        )


@pytest.fixture(scope="session")
def require_doris() -> None:
    """Doris 相关用例的前置条件。"""
    missing = [name for name in DORIS_CONTAINERS if not container_running(name)]
    if missing:
        pytest.skip(
            f"以下 Doris 容器未运行：{', '.join(missing)}。\n"
            "  Doris 首次启动需要 1~3 分钟，请稍后重试：\n"
            "  docker compose logs --tail=50 doris-fe doris-be"
        )


@pytest.fixture(scope="session")
def mysql_creds() -> tuple[str, str]:
    assert MYSQL_ROOT_PASSWORD, "MYSQL_ROOT_PASSWORD 未设置，请检查 .env"
    return "root", MYSQL_ROOT_PASSWORD


# ============================================================
# 1. MySQL 可以连接
# ============================================================
def test_mysql_connection(mysql_creds: tuple[str, str]) -> None:
    user, password = mysql_creds
    out = docker_exec_out(
        "mysql", "mysqladmin", "ping", "-h", "127.0.0.1", "-u", user, f"-p{password}", "--silent"
    )
    assert "alive" in out.lower()


def test_mysql_version_is_8x(mysql_creds: tuple[str, str]) -> None:
    user, password = mysql_creds
    out = docker_exec_out(
        "mysql", "mysql", "-h", "127.0.0.1", "-u", user, f"-p{password}", "-N", "-B",
        "-e", "SELECT VERSION();",
    ).strip()
    major = int(out.split(".")[0])
    assert major == 8, f"期望 MySQL 8.x，实际 {out}"


# ============================================================
# 2. ecommerce 数据库存在
# ============================================================
def test_ecommerce_database_exists(mysql_creds: tuple[str, str]) -> None:
    user, password = mysql_creds
    out = docker_exec_out(
        "mysql", "mysql", "-h", "127.0.0.1", "-u", user, f"-p{password}", "-N", "-B",
        "-e",
        f"SELECT COUNT(*) FROM information_schema.schemata "
        f"WHERE schema_name='{MYSQL_DATABASE}';",
    ).strip()
    assert out == "1", f"数据库 {MYSQL_DATABASE} 不存在"


# ============================================================
# 3. 业务表存在（并检查字段数，确保不是空壳表）
# ============================================================
@pytest.mark.parametrize("table", MYSQL_TABLES)
def test_mysql_table_exists(table: str, mysql_creds: tuple[str, str]) -> None:
    user, password = mysql_creds
    out = docker_exec_out(
        "mysql", "mysql", "-h", "127.0.0.1", "-u", user, f"-p{password}", "-N", "-B",
        "-e",
        f"SELECT COUNT(*) FROM information_schema.columns "
        f"WHERE table_schema='{MYSQL_DATABASE}' AND table_name='{table}';",
    ).strip()
    assert int(out) > 0, f"表 {MYSQL_DATABASE}.{table} 不存在"


def test_mysql_tables_have_expected_columns(mysql_creds: tuple[str, str]) -> None:
    """校验关键字段确实存在（依据 SPRINT_0.md 第 7 节）。"""
    user, password = mysql_creds
    expected = {
        "user": {"user_id", "username", "gender", "age", "province", "city",
                 "user_level", "register_time", "update_time"},
        "product": {"product_id", "product_name", "category_id", "category_name",
                    "brand", "price", "cost", "status", "create_time", "update_time"},
        "orders": {"order_id", "user_id", "product_id", "quantity", "amount",
                   "status", "create_time", "pay_time", "update_time"},
        "payment": {"payment_id", "order_id", "user_id", "amount",
                    "payment_method", "payment_status", "payment_time"},
        "refund": {"refund_id", "order_id", "user_id", "refund_amount",
                   "refund_reason", "refund_status", "refund_time"},
    }

    for table, columns in expected.items():
        out = docker_exec_out(
            "mysql", "mysql", "-h", "127.0.0.1", "-u", user, f"-p{password}", "-N", "-B",
            "-e",
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema='{MYSQL_DATABASE}' AND table_name='{table}';",
        )
        actual = {line.strip() for line in out.splitlines() if line.strip()}
        missing = columns - actual
        assert not missing, f"表 {table} 缺少字段：{sorted(missing)}"


def test_app_user_can_access_ecommerce(mysql_creds: tuple[str, str]) -> None:
    """业务账号 app 应能连接并读取 ecommerce。"""
    assert MYSQL_PASSWORD, "MYSQL_PASSWORD 未设置"
    out = docker_exec_out(
        "mysql", "mysql", "-h", "127.0.0.1", "-u", MYSQL_USER, f"-p{MYSQL_PASSWORD}",
        "-N", "-B", "-e",
        f"SELECT COUNT(*) FROM information_schema.tables "
        f"WHERE table_schema='{MYSQL_DATABASE}';",
    ).strip()
    assert int(out) >= len(MYSQL_TABLES)


# ============================================================
# 4. Kafka Topic 存在（含分区数校验）
# ============================================================
@pytest.mark.parametrize("topic", KAFKA_TOPICS)
def test_kafka_topic_exists(topic: str) -> None:
    out = docker_exec_out(
        "kafka", "/opt/kafka/bin/kafka-topics.sh",
        "--bootstrap-server", "kafka:9092", "--list",
    )
    assert topic in out.split(), f"Topic {topic} 不存在"


@pytest.mark.parametrize("topic", KAFKA_TOPICS)
def test_kafka_topic_partitions(topic: str) -> None:
    """每个 Topic 必须有 3 个分区（SPRINT_0.md 第 8 节）。

    注意：kafka-topics.sh --describe 的输出首行是 Topic 概要行
    （Topic: xxx  TopicId: ...  PartitionCount: 3 ...），
    其后每个分区一行且**以制表符开头**。因此不能用
    "以 Topic: 开头" 来数分区（那样会多算首行），
    而应统计同时含 Partition: 与 Leader: 的行。
    """
    out = docker_exec_out(
        "kafka", "/opt/kafka/bin/kafka-topics.sh",
        "--bootstrap-server", "kafka:9092", "--describe", "--topic", topic,
    )

    partition_lines = [
        line for line in out.splitlines()
        if "Partition:" in line and "Leader:" in line
    ]
    assert len(partition_lines) == 3, (
        f"Topic {topic} 分区数为 {len(partition_lines)}，期望 3\n{out}"
    )
    # 副本因子必须为 1（单机环境）
    assert "ReplicationFactor: 1" in out, f"Topic {topic} 副本因子不是 1\n{out}"


# ============================================================
# 5 & 6. Kafka 可以生产消息 / 消费消息
# ============================================================
def test_kafka_produce_and_consume() -> None:
    """端到端验证：Producer -> Kafka -> Consumer。

    使用独立测试 Topic，避免污染业务 Topic；测试结束删除。
    """
    topic = f"smoke_test_{uuid.uuid4().hex[:8]}"

    # --- 创建测试 Topic ---
    docker_exec_out(
        "kafka", "/opt/kafka/bin/kafka-topics.sh",
        "--bootstrap-server", "kafka:9092",
        "--create", "--topic", topic,
        "--partitions", "1", "--replication-factor", "1",
    )

    try:
        # --- 生产消息 ---
        payload = json.dumps(
            {"event_id": "evt_smoke_1", "event_type": "SMOKE_TEST", "value": 42},
            ensure_ascii=False,
        )
        produce = subprocess.run(
            [
                "docker", "exec", "-i", "kafka",
                "/opt/kafka/bin/kafka-console-producer.sh",
                "--bootstrap-server", "kafka:9092",
                "--topic", topic,
            ],
            input=payload + "\n",
            capture_output=True, text=True, timeout=60, check=False, errors="replace",
        )
        assert produce.returncode == 0, f"生产消息失败：{produce.stderr}"

        # --- 消费消息 ---
        consume = docker_exec(
            "kafka",
            "/opt/kafka/bin/kafka-console-consumer.sh",
            "--bootstrap-server", "kafka:9092",
            "--topic", topic,
            "--from-beginning",
            "--max-messages", "1",
            "--timeout-ms", "30000",
            timeout=90,
        )
        consumed = consume.stdout.strip()
        assert consumed, f"未消费到消息。stderr={consume.stderr}"

        # --- 校验内容 ---
        parsed = json.loads(consumed.splitlines()[-1])
        assert parsed["event_id"] == "evt_smoke_1"
        assert parsed["value"] == 42

    finally:
        docker_exec(
            "kafka", "/opt/kafka/bin/kafka-topics.sh",
            "--bootstrap-server", "kafka:9092", "--delete", "--topic", topic,
        )


# ============================================================
# 7. MinIO Bucket 存在
# ============================================================
def test_minio_healthy() -> None:
    """MinIO 必须处于 healthy 状态。

    注意：minio 镜像基于 BusyBox，**不含 curl / wget / nc**，
    因此不能用 `docker exec minio curl .../minio/health/live`。
    这里读取 compose healthcheck 的实际结果（该 healthcheck 本身
    用 mc 探测，见 infrastructure/minio/healthcheck.sh）。
    """
    result = subprocess.run(
        [DOCKER, "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", "minio"],
        capture_output=True, text=True, check=False,
    )
    status = result.stdout.strip()
    assert status == "healthy", f"MinIO 健康状态为 {status!r}，期望 'healthy'"


def test_minio_bucket_exists() -> None:
    """通过 mc 校验 lakehouse bucket 存在。"""
    assert MINIO_ROOT_USER and MINIO_ROOT_PASSWORD, "MinIO 凭据未设置"
    script = (
        f'MC_HOST_local="http://{MINIO_ROOT_USER}:{MINIO_ROOT_PASSWORD}@localhost:9000"; '
        f"export MC_HOST_local; mc ls local/"
    )
    out = docker_exec_out("minio", "sh", "-c", script)
    assert MINIO_BUCKET in out, f"bucket {MINIO_BUCKET} 不存在\n{out}"


# ============================================================
# 8. Doris 可以连接
# ============================================================
def test_doris_fe_reachable(require_doris: None) -> None:
    """FE 应能通过 MySQL 协议连接。"""
    out = docker_exec_out(
        "doris-be", "mysql", "-h", DORIS_FE_IP, "-P", "9030", "-uroot",
        "--connect-timeout=10", "-N", "-B", "-e", "SELECT 1;",
    ).strip()
    assert out == "1", f"Doris FE 连接异常，返回 {out!r}"


def test_doris_ecommerce_database_exists(require_doris: None) -> None:
    out = docker_exec_out(
        "doris-be", "mysql", "-h", DORIS_FE_IP, "-P", "9030", "-uroot",
        "--connect-timeout=10", "-N", "-B",
        "-e", "SHOW DATABASES LIKE 'ecommerce';",
    ).strip()
    assert out == "ecommerce", f"ecommerce 库不存在。实际输出：{out!r}"


# ============================================================
# 9. Doris 可以建表
# ============================================================
def test_doris_can_create_table(require_doris: None) -> None:
    """验证 Doris 可以建表（任务书第 19 节第 9 项）。

    稳定性说明：Doris 在 BE 重新注册后的一小段时间内，
    新建 tablet 可能报
      Failed to create partition[...] Timeout:30 seconds
    这是 BE 尚在恢复的正常现象，因此这里给较长超时并重试，
    同时对 DROP/查询也给足超时，避免测试因环境抖动误报失败。
    """
    table = f"smoke_test_{uuid.uuid4().hex[:8]}"
    full = f"ecommerce.{table}"
    ddl = (
        f"CREATE TABLE {full} ("
        f"  id BIGINT, message VARCHAR(255), create_time DATETIME"
        f") DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
        f"PROPERTIES ('replication_num' = '1');"
    )

    def run(sql: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return docker_exec(
            "doris-be", "mysql", "-h", DORIS_FE_IP, "-P", "9030", "-uroot",
            "--connect-timeout=10", "-e", sql, timeout=timeout,
        )

    created = False
    last: subprocess.CompletedProcess | None = None
    try:
        for attempt in range(3):
            last = run(ddl)
            if last.returncode == 0:
                created = True
                break
            # BE 刚重新注册时建 tablet 可能超时，等待后重试
            time.sleep(10)

        assert created, (
            "Doris 建表失败（已重试 3 次）：\n"
            f"{last.stderr if last else '<no result>'}"
        )

        out = docker_exec_out(
            "doris-be", "mysql", "-h", DORIS_FE_IP, "-P", "9030", "-uroot",
            "--connect-timeout=10", "-N", "-B",
            "-e",
            f"SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema='ecommerce' AND table_name='{table}';",
            timeout=60,
        ).strip()
        assert out == "1", f"建表后未查到 {table}"
    finally:
        run(f"DROP TABLE IF EXISTS {full};", timeout=120)


# ============================================================
# 10. Doris 可以查询测试数据
# ============================================================
def test_doris_test_connection_table_query(require_doris: None) -> None:
    """验证 SPRINT_0.md 第 11 节要求的 test_connection 表与数据。"""
    out = docker_exec_out(
        "doris-be", "mysql", "-h", DORIS_FE_IP, "-P", "9030", "-uroot",
        "--connect-timeout=10", "-N", "-B",
        "-e", "SELECT id, message FROM ecommerce.test_connection ORDER BY id;",
    )
    assert "doris connection ok" in out, (
        f"test_connection 表缺少测试数据。实际输出：{out!r}\n"
        "请检查 doris-be 容器日志中的初始化 SQL 执行结果。"
    )


def test_doris_backend_alive(require_doris: None) -> None:
    """BE 必须已注册且 Alive。

    说明：BE 在 FE 中登记的 Host 可能是静态 IP（DORIS_BE_IP，
    由 BE_ADDR 决定）或容器主机名（doris-be），取决于启动路径。
    因此这里接受两者之一，只要求「该 BE 已注册且 Alive=true」。
    """
    out = docker_exec_out(
        "doris-be", "mysql", "-h", DORIS_FE_IP, "-P", "9030", "-uroot",
        "--connect-timeout=10", "-N", "-B", "-e", "SHOW BACKENDS;",
    )
    assert DORIS_BE_IP in out or "doris-be" in out, (
        f"未找到已注册的本机 BE（期望 {DORIS_BE_IP} 或 doris-be）\n{out}"
    )
    assert "true" in out, f"BE 未存活（Alive 列无 true）\n{out}"


def test_doris_requires_explicit_replication_num(require_doris: None) -> None:
    """单 BE 环境下，Doris 默认 replication_num=3，必须显式指定为 1。

    注意：Doris 4.1.4 **不存在** `default_replication_num` 系统变量
    （`ALTER SYSTEM SET` 会报 mismatched input，`SET` 会报
     Unknown system variable），因此不能通过查询该变量来验证。
    这里改为验证真正影响业务的约束：

      不带 PROPERTIES 建表应当失败（默认 3 副本 > 1 个 BE），
      带 PROPERTIES('replication_num'='1') 建表应当成功。

    稳定性说明：Doris 刚完成 BE 注册时元数据操作可能较慢，
    因此此处给建表较长的超时，并在建表失败时重试一次。
    """
    table = f"smoke_repl_{uuid.uuid4().hex[:8]}"
    full = f"ecommerce.{table}"

    ddl_no_props = (
        f"CREATE TABLE {full}_a (id BIGINT) "
        f"DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1;"
    )
    ddl_with_props = (
        f"CREATE TABLE {full} (id BIGINT) "
        f"DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
        f"PROPERTIES('replication_num'='1');"
    )

    def run(sql: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return docker_exec(
            "doris-be", "mysql", "-h", DORIS_FE_IP, "-P", "9030", "-uroot",
            "--connect-timeout=10", "-e", sql, timeout=timeout,
        )

    def drop_if_exists(name: str) -> None:
        run(f"DROP TABLE IF EXISTS {name};", timeout=120)

    # 1) 不带 PROPERTIES -> 预期失败（默认副本 3 > 可用 BE 1）
    no_props = run(ddl_no_props)
    if no_props.returncode == 0:
        drop_if_exists(f"{full}_a")
        pytest.fail(
            "不带 PROPERTIES 的建表竟然成功了；"
            "单 BE 环境预期应以「replication num 3 > available backend 1」失败"
        )
    drop_if_exists(f"{full}_a")

    # 2) 带 PROPERTIES -> 预期成功（允许重试一次，规避刚注册完成的抖动）
    created = False
    last: subprocess.CompletedProcess | None = None
    for _ in range(2):
        last = run(ddl_with_props)
        if last.returncode == 0:
            created = True
            break
        time.sleep(5)

    try:
        assert created, (
            f"带 PROPERTIES('replication_num'='1') 的建表失败：\n"
            f"{last.stderr if last else ''}"
        )
        out = docker_exec_out(
            "doris-be", "mysql", "-h", DORIS_FE_IP, "-P", "9030", "-uroot",
            "--connect-timeout=10", "-N", "-B", "-e",
            f"SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema='ecommerce' AND table_name='{table}';",
            timeout=60,
        ).strip()
        assert out == "1", f"建表后未查到 {table}"
    finally:
        drop_if_exists(full)
