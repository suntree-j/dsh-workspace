"""
离线湖仓流水线（Sprint 4）
==========================

把 Sprint 3 的**手工**批处理变成按依赖调度的 DAG。

在 Sprint 4 之前，离线链路的执行方式是：

    bash scripts/batch-mode.sh          # 人手工敲，跑完就完了

这带来两个问题：无法无人值守；没有「哪一层失败了、重试了几次」的记录。

本 DAG 的做法
-------------

    pause_realtime ── ods_extract ── dwd ── dws ── ads ── reconcile ── load ── restore_realtime

三个关键设计点
--------------

**1. 每个阶段是独立的 Airflow 任务，而不是把 batch-mode.sh 整个塞进一个任务**

    Airflow 的价值在于每一层的可见性、独立重试与失败定位。
    塞成一个任务的话，Airflow 就只是个定时器，退化成 cron 了。

**2. 暂停与恢复必须是 DAG 里的两个独立任务**

    因为恢复任务要能用 ``trigger_rule=all_done``：**任一上游失败也必须恢复
    实时链路**。否则一次失败的批处理会让看板一直停在暂停状态 ——
    那比批处理失败本身严重得多（看板停了没人知道，比任务红了没人看更糟）。

**3. max_active_runs=1：绝不允许两次批处理重叠**

    本机实时链路常驻约 12 GB，可用内存只有约 2.5 GB，而批处理的内存闸门是
    MIN_AVAILABLE_MB_FOR_BATCH=3000。所以必须「先暂停实时链路（释放约 2.8 GB）
    再跑批」。两次重叠 = 必然打穿内存 = Sprint 3 那次整机失联的形态。

为什么调度在凌晨三点
--------------------

    批处理期间实时链路是暂停的（看板曲线会延后追上）。
    选在使用者最少的时间窗，把「暂停」的影响降到最低。

闸门在哪里
----------

    不在这个文件里，而在 ``scripts/run-batch-pipeline.sh`` 里 ——
    它 source 了 ``lib/memory-guard.sh``，每个阶段开跑前都会过：

      - require_no_running_jobs()   不允许叠加第二个 Spark 作业
      - require_memory_for_batch()  可用内存必须 >= 3000 MB

    这样「手工跑」和「调度跑」受**同一套**闸门约束，不存在两条路径行为不一致。
    这一点是刻意的：调度必须复用人工入口，不能平行实现一套。
"""

from __future__ import annotations

from datetime import timedelta

import pendulum

# Airflow 3 的公开 DAG 编写接口是 airflow.sdk（不再是 airflow.models）
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 仓库根目录。DAG 只调用仓库里的脚本，不自己实现业务逻辑 ——
#: 这样「人手工跑的命令」与「调度跑的命令」永远是同一个东西。
REPO = "/opt/data-platform"

#: 每天凌晨 3 点（Asia/Shanghai）。理由见模块文档。
SCHEDULE = "0 3 * * *"

# !! 不要把 retries / execution_timeout 只写在这里 !!
#
#   实测（Airflow 3.3.2）：`@dag(default_args=...)` 里的这些键**不生效**。
#   证据：任务实例日志里 `try_number=1, max_tries=1` ——
#   即 retries 实际为 0，尽管 default_args 里写的是 1。
#   后果不是"少重试一次"这么轻：
#     状态会停在 `up_for_retry` 却已无重试次数，
#     于是**永久卡在中间态** —— DAG run 永不结束、restore_realtime 永不执行、
#     实时链路一直停着，而且 max_active_runs=1 还会挡住后续所有 run。
#     实测就是这样卡住的。
#
#   所以：重试与超时一律**显式写在每个算子上**（见 _stage 与 restore）。
#   这里只保留不影响调度的元信息。
DEFAULT_ARGS = {
    "owner": "data-platform",
    "depends_on_past": False,
    "email_on_failure": False,
}

#: 单层的重试策略。放在常量里是为了让每个算子引用同一份值，
#: 而不是各处手写导致不一致。
STAGE_RETRIES = 1
STAGE_RETRY_DELAY = timedelta(minutes=3)
#: 单层执行超时。实测基线：dwd 3.4 分钟 / dws 5.7 分钟 / ads 约 6~8 分钟。
#: 给 45 分钟：足够容纳数据量增长，又能在真挂死时及时止损 ——
#: 没有超时时，"任务永久挂住"比"任务失败"严重得多（失败至少会触发恢复）。
STAGE_TIMEOUT = timedelta(minutes=45)


def _stage(task_id: str, stage: str, doc: str) -> BashOperator:
    """构造一个「跑某一层」的任务。

    为什么统一走 ``run-batch-pipeline.sh --stage``：
        它是 Sprint 3 建立的**人工入口**。调度复用它，两个好处：

        1. 不存在「手工能跑、调度跑不了」的行为分叉；
        2. 内存闸门与「不叠加作业」闸门自动生效（在脚本里，不在这里）。
    """
    return BashOperator(
        task_id=task_id,
        bash_command=f"bash {REPO}/scripts/run-batch-pipeline.sh --stage {stage}",
        cwd=REPO,
        append_env=True,
        # 显式写在算子上，不依赖 default_args（理由见 DEFAULT_ARGS 上方的说明）
        retries=STAGE_RETRIES,
        retry_delay=STAGE_RETRY_DELAY,
        execution_timeout=STAGE_TIMEOUT,
        doc_md=doc,
    )


@dag(
    dag_id="offline_lakehouse_pipeline",
    description="离线湖仓流水线：暂停实时链路 → 逐层批处理 → 对账 → 装载 → 恢复实时链路",
    schedule=SCHEDULE,
    start_date=pendulum.datetime(2026, 9, 1, tz="Asia/Shanghai"),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["sprint4", "offline", "lakehouse"],
    doc_md=__doc__,
)
def offline_lakehouse_pipeline() -> None:
    # ---- 1. 暂停实时链路（为批处理腾出约 2.8 GB）----
    pause = BashOperator(
        task_id="pause_realtime",
        bash_command=f"bash {REPO}/scripts/batch-mode.sh --pause-only",
        cwd=REPO,
        append_env=True,
        # 显式写在算子上（不依赖 default_args，理由见文件上方说明）。
        # 暂停本身很快，但失败必须重试：暂停不成功的话后面每个 stage
        # 都会被内存闸门拒绝，整条 DAG 白跑。
        retries=STAGE_RETRIES,
        retry_delay=STAGE_RETRY_DELAY,
        execution_timeout=timedelta(minutes=15),
        doc_md=(
            "暂停 Flink 栈，释放约 1.65 GB 内存（实测 2282 → 3935 MB）。\n\n"
            "不会丢数据：Kafka 是缓冲（Flink 停了事件继续堆在 topic 里），"
            "Flink 的消费位点在 checkpoint 里，重启后从上次位置继续，"
            "重复的部分由 Doris 的 UNIQUE KEY 幂等覆盖。\n\n"
            "唯一影响是暂停期间看板上的实时曲线会延后追上。"
        ),
    )

    # ---- 2. 抽取：MySQL → ODS ----
    ods = _stage(
        "ods_extract",
        "ods",
        "MySQL 业务库 → 湖仓 ODS（Spark JDBC 抽取，作业内自带逐表对账）。",
    )

    # ---- 3. 归档：Kafka 行为事件 → ODS（Sprint 4 新增）----
    # 补齐 Sprint 3 记录的设计缺口：流量域在 MySQL 里没有事实表，
    # 离线侧"无源可算"，因此当时只有交易域参与批流对账。
    # 归档落地后，流量域也能离线计算并与实时链路逐窗口对账。
    #
    # 为什么与 ods_extract **串行**而不是并行：
    #   两者数据源不同、确实互不依赖，但本机可用内存只够一次跑一个 Spark 作业
    #   （实测：暂停实时链路后约 3.9 GB，单个 Spark 驱动+执行器约 1.5 GB）。
    #   并行会同时拉起两个驱动，直接打穿内存 —— 那正是 Sprint 3 整机失联的形态。
    #   DAG 里没有并行，是**有意的**。
    archive = _stage(
        "archive_behavior",
        "archive",
        "Kafka `behavior_event` → 湖仓 ODS（批读 earliest→latest，按 dt 动态分区覆盖，幂等）。\n\n"
        "作业内自检：归档行数 == Kafka 实际消息数、漏斗单调收窄、"
        "枚举合法、`user_id` 均存在于 `ods_user`。",
    )

    # ---- 4~6. 分层建模 ----
    dwd = _stage("dwd_layers", "dwd", "ODS → DWD：去重 / 清洗 / 维度补全。")
    dws = _stage("dws_layers", "dws", "DWD → DWS：按天轻度聚合，只出可加指标。")
    ads = _stage("ads_layers", "ads", "DWD → ADS：指标口径（1 分钟 + 1 天）。")

    # ---- 7. 批流交叉对账 ----
    reconcile = _stage(
        "reconcile",
        "reconcile",
        "实时 ADS ↔ 离线 ADS 逐窗口比对，差异不为 0 即失败。\n\n"
        "这是整条离线链路存在的意义所在：不是「也算了一遍」，"
        "而是「算出同一个数，并且有证据」。",
    )

    # ---- 8. 装载进服务库 ----
    # 必须排在 reconcile 之后：要装载的表里包含对账结果表。
    # 顺序错了会表现为「前几张表成功、对账表报装载失败」
    # （S3() TVF 匹配不到文件时静默返回空）。
    load = _stage(
        "load_to_doris",
        "load",
        "离线结果 → Doris lakehouse_ads（S3() TVF 直读 Parquet），供只读服务查询。",
    )

    # ---- 9. 恢复实时链路 ----
    # !! trigger_rule="all_done" 是这里最要紧的一个参数 !!
    #   默认的 all_success 会让「上游一失败就不恢复」，
    #   结果是实时链路被永久留在暂停状态，看板一直不动。
    #   用 all_done：无论成功、失败还是被跳过，都会执行恢复。
    restore = BashOperator(
        task_id="restore_realtime",
        bash_command=f"bash {REPO}/scripts/batch-mode.sh --restore-only",
        cwd=REPO,
        append_env=True,
        trigger_rule="all_done",
        # 恢复比批处理本身更「不能失败」，所以多给两次重试
        retries=2,
        retry_delay=timedelta(minutes=2),
        # 恢复自身的超时单独给：它包含 Flink 集群起来 + 作业续跑 +
        # 最多 180 秒的健康检查重试；15 分钟足够，且不与 stage 的 45 分钟混同。
        execution_timeout=timedelta(minutes=15),
        doc_md=(
            "恢复 Flink 集群并跑 health-check 自检（退出码即任务结果）。\n\n"
            "`trigger_rule=all_done`：**任一上游失败也必须执行**。"
            "否则一次失败的批处理会让看板永久停在暂停状态。"
        ),
    )

    pause >> ods >> archive >> dwd >> dws >> ads >> reconcile >> load >> restore


offline_lakehouse_pipeline()
