"""Sprint 3 离线分层作业的公共工具。

被 build_dwd.py / build_dws.py / build_ads.py / reconcile_batch_realtime.py 复用。

为什么把这些放在一个模块里：
    4 个作业都要做同样三件事 —— 建表(DDL)、执行分层 SQL、跑断言。
    如果每个作业各写一份，改一处断言逻辑就要改四处，
    很容易出现"某个作业的校验被悄悄改弱"的情况（AGENTS.md 第 8.5 节禁止弱化测试）。
"""

from __future__ import annotations

import re
from pathlib import Path

from pyspark.sql import SparkSession

# 与 spark-defaults.conf / scripts/lib/spark-job.sh 的 spark.jobs.* 对应
DEFAULT_SQL_DIR = "/opt/sql/hive"
# 分层作业自带的 SQL 目录（容器内挂载点，见 docker-compose 的 spark-submit 卷）
DEFAULT_LAYER_SQL_DIR = "/opt/layer-sql"

# DDL 里需要顺序执行的建表文件（顺序有依赖：ODS 最先，对账表最后）
DDL_FILES = (
    "01_ods_tables.sql",
    "02_dwd_tables.sql",
    "03_dws_tables.sql",
    "04_ads_tables.sql",
    "05_reconcile_tables.sql",
)


def sql_dir(spark: SparkSession) -> Path:
    """建表 DDL（sql/hive/*.sql）所在目录，默认 /opt/sql/hive。"""
    return Path(spark.sparkContext.getConf().get("spark.jobs.sqlDir", DEFAULT_SQL_DIR))


def jobs_sql_dir(spark: SparkSession | None = None) -> Path:
    """分层作业自带的 SQL 目录（infrastructure/spark/sql）。

    !! 为什么不能从 __file__ 推导 !!
       作业脚本挂在 /opt/jobs（只读），而 Docker 不允许在只读挂载点下
       再创建挂载点（实测：mkdirat .../opt/jobs/sql: read-only file system）。
       所以分层 SQL 挂在**平级**目录 /opt/layer-sql，
       由 spark-submit 通过 spark.jobs.layerSqlDir 告知作业。
    """
    if spark is not None:
        configured = spark.sparkContext.getConf().get("spark.jobs.layerSqlDir", "")
        if configured:
            return Path(configured)
    return Path(DEFAULT_LAYER_SQL_DIR)


def build_spark(app_name: str) -> SparkSession:
    """创建启用 Hive catalog 的 SparkSession（其余配置来自 spark-defaults.conf）。"""
    spark = (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.catalogImplementation", "hive")
        .enableHiveSupport()
        .getOrCreate()
    )
    # 幂等写入依赖"按分区覆盖"，必须开启动态分区覆盖；
    # 否则 INSERT OVERWRITE 一个分区会把整表清空（这是 Spark 的默认行为，极易踩坑）。
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")
    spark.sparkContext.setLogLevel("WARN")
    return spark


def _split_statements(content: str) -> list[str]:
    """按分号切分 SQL 文件；跳过空行与整行注释。

    与本项目 Sprint 2 抽取作业的切分方式保持一致：
    只支持"行尾分号"这一种写法，够用且不会把函数体里的分号切错。
    """
    statements: list[str] = []
    buffer: list[str] = []
    for raw in content.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("--"):
            continue
        buffer.append(line)
        if line.endswith(";"):
            statements.append("\n".join(buffer).rstrip(";").strip())
            buffer = []
    if buffer:
        statements.append("\n".join(buffer).strip())
    return [s for s in statements if s]


def run_ddl(spark: SparkSession, ddl_path: str) -> int:
    """执行一个 DDL 文件（全部 IF NOT EXISTS，可重复执行）。"""
    content = Path(ddl_path).read_text(encoding="utf-8")
    statements = _split_statements(content)
    print(f"[ddl] {ddl_path} → {len(statements)} 条语句", flush=True)
    for statement in statements:
        spark.sql(statement)
        print(f"[ddl]   OK  {' '.join(statement.split())[:86]} ...", flush=True)
    return len(statements)


def ensure_tables(spark: SparkSession, sql_dir_override: str | None = None) -> None:
    """按顺序执行全部建表 DDL。"""
    base = Path(sql_dir_override) if sql_dir_override else sql_dir(spark)
    for name in DDL_FILES:
        path = base / name
        if not path.exists():
            raise FileNotFoundError(f"缺少建表脚本：{path}")
        run_ddl(spark, str(path))


_PLACEHOLDER = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def run_sql_file(
    spark: SparkSession,
    sql_path: str,
    variables: dict[str, str] | None = None,
    base_dir: str | None = None,
) -> list[str]:
    """执行一个分层 SQL 文件。

    - `${VAR}` 会被 variables 里的值替换；未提供的占位符**直接报错**，
      不允许留到 Spark 里变成语法错误（那样报错信息完全看不出根因）。
    - `@include xxx.sql` 会被替换为同目录下该文件的内容，
      用于把重复片段（例如事件时间映射）集中到一处定义。
    - sql_path 为绝对路径时直接使用；否则相对 base_dir（默认作业自带的 sql 目录）解析。
    """
    base = Path(base_dir) if base_dir else jobs_sql_dir(spark)
    path = Path(sql_path)
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        raise FileNotFoundError(f"找不到 SQL 文件：{path}")
    content = path.read_text(encoding="utf-8")

    variables = variables or {}
    missing = {m for m in _PLACEHOLDER.findall(content) if m not in variables}
    if missing:
        raise KeyError(f"{path.name} 中未提供取值的占位符：{sorted(missing)}")
    content = _PLACEHOLDER.sub(lambda m: variables[m.group(1)], content)
    content = _expand_includes(content, path.parent)

    statements = _split_statements(content)
    print(f"[sql ] {path.name} → {len(statements)} 条语句", flush=True)
    for statement in statements:
        head = " ".join(statement.split())[:96]
        print(f"[sql ]   RUN {head} ...", flush=True)
        spark.sql(statement)
    return statements


def run_sql_text(spark: SparkSession, name: str, text: str) -> None:
    """执行一段内联 SQL（用于作业内的自检语句）。"""
    statements = _split_statements(text)
    for statement in statements:
        print(f"[sql ]   RUN [{name}] {' '.join(statement.split())[:90]} ...", flush=True)
        spark.sql(statement)


def _expand_includes(content: str, base_dir: Path) -> str:
    """把 `@include file.sql` 展开为文件内容（支持一层嵌套即可）。"""
    out: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("@include "):
            target = base_dir / stripped.split(None, 1)[1].strip()
            if not target.exists():
                raise FileNotFoundError(f"@include 目标不存在：{target}")
            out.append(target.read_text(encoding="utf-8"))
        else:
            out.append(line)
    return "\n".join(out)


def describe_columns(spark: SparkSession, qualified_table: str) -> dict[str, str]:
    """返回 {列名: 类型}，用于字段类型断言。

    !! 为什么必须 strip()（Sprint 3 踩坑）!!
        Spark 的 DESCRIBE 输出里，**列名会被右填充空格对齐**（打印宽度对齐），
        而类型列不会：命令行下看到的是
            amount              \\tdecimal(18,2)
        于是：
            - DataFrame API 取 ["col_name"] 得到的可能是带尾空格的字符串；
            - 用 awk '$1=="amount"' 解析 CLI 输出则**永远匹配不到**，
              表现为"取不到类型"的空值（而不是报错），极易误判成类型错误。
        这里统一 strip，且把"取不到"明确表示为 None 的缺失项。
    """
    rows = spark.sql(f"DESCRIBE {qualified_table}").collect()
    out: dict[str, str] = {}
    for row in rows:
        name = (row["col_name"] or "").strip()
        if not name or name.startswith("#"):
            continue  # 跳过 "# Partition Information" 这类分隔行
        out[name] = (row["data_type"] or "").strip()
    return out


def clean_stale_spark_temp(spark: SparkSession, table_paths: tuple[str, ...]) -> int:
    """清理 Spark 写入残留的临时目录（.spark-staging-* / _temporary）。

    !! 为什么必须清（实测踩坑）!!
      带分区的 INSERT OVERWRITE 会先在表目录下写
        <表目录>/.spark-staging-<uuid>/_temporary/0/task_.../dt=.../part-*.parquet
      成功后再原子地搬到 dt=... 并删掉 staging 目录。
      如果作业中途被杀（本项目因为内存不足被杀过一次），staging 目录会**留在
      S3 上**：它不会被自动清理，而下游 Doris 的 S3() TVF 用
      `**/*.parquet` 递归读取时会把这份残留数据**再读一遍**，
      导致装载行数凭空翻倍 —— 而且是"看起来多了一半数据"这种
      最难怀疑到根因的现象。

    只在作业开始时清理，不碰 dt=* 这些正式分区。
    """
    jvm = spark._jvm  # noqa: SLF001 - PySpark 没有公开的 FS API，只能走 JVM 网关
    conf = spark._jsc.hadoopConfiguration()  # noqa: SLF001
    removed = 0
    for path in table_paths:
        root = jvm.org.apache.hadoop.fs.Path(path)
        fs = root.getFileSystem(conf)
        if not fs.exists(root):
            continue
        for status in fs.listStatus(root):
            name = status.getPath().getName()
            if name.startswith(".spark-staging-") or name == "_temporary":
                fs.delete(status.getPath(), True)
                removed += 1
    return removed


def exact_distinct(spark: SparkSession, expression: str, source: str) -> int:
    """精确去重计数。

    !! 为什么不用 COUNT(DISTINCT x)（Sprint 3 踩坑）!!
        Spark SQL 的 COUNT(DISTINCT x) 默认走 **HyperLogLog 近似算法**
       （spark.sql.optimizer.useExactDistinct 相关实现，误差上限约 5%）。
        在 6000 行规模上，同一个"不同类目数"会被算成 10 或 11、
        "不同窗口数"会被算成 5954 而实际是 5954/5998 之间任意值。
        用它做**精确对账断言**会得到随机结果：
        时而通过、时而失败，且失败时看起来像"数据丢了一半"（实测：
        1d 类目表 2834 行被判成"应为 5998 行"，排查了两轮才发现是近似函数）。
        size(collect_set(x)) 是精确实现，在本题数据量下开销可忽略。
    """
    return int(spark.sql(f"SELECT SIZE(COLLECT_SET({expression})) AS c FROM {source}").first()["c"])


class Checker:
    """作业内自检器：收集断言结果，末尾统一汇总，有失败就以非 0 退出。

    为什么作业要自己断言（而不是只让外层脚本查）：
        作业跑完到外层脚本查询之间存在窗口，中间可能有别的进程改数据；
        把断言放在作业内，得到的结论对应的是"作业刚写完的那一刻"。
    """

    def __init__(self, title: str) -> None:
        self.title = title
        self.rows: list[tuple[str, str, bool, str]] = []

    def check(self, name: str, actual: object, expected: object) -> bool:
        ok = actual == expected
        self.rows.append((name, str(actual), ok, f"期望 {expected}"))
        return ok

    def check_true(self, name: str, actual: object, note: str = "") -> bool:
        ok = bool(actual)
        self.rows.append((name, str(actual), ok, note))
        return ok

    def report(self) -> tuple[int, int]:
        print("", flush=True)
        print(f"[check] ===== {self.title} =====", flush=True)
        failed = 0
        for name, actual, ok, note in self.rows:
            flag = "OK  " if ok else "FAIL"
            if not ok:
                failed += 1
            detail = f"  ({note})" if (note and not ok) else ""
            print(f"[check]   {flag} {name:<44} {actual}{detail}", flush=True)
        print(f"[check] ===== {len(self.rows) - failed}/{len(self.rows)} 通过 =====", flush=True)
        return len(self.rows), failed

    def finish(self) -> int:
        total, failed = self.report()
        if failed:
            print(f"[result] 失败：{failed}/{total} 项校验未通过 —— 不继续往下游写数据", flush=True)
            return 1
        print(f"[result] 成功：{total} 项校验全部通过", flush=True)
        return 0
