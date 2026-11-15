"""归档自检的纯判据（不依赖 pyspark，便于单测）。

由 `archive_behavior_events.py` 导入使用；单独成文件的原因是
作业脚本顶层 `import pyspark`，在无 Spark 的环境里无法 import 做单测。
"""

from __future__ import annotations


def archive_criteria(
    kafka_count: int,
    archived_total: int,
    archived_touched: int,
    parsed_count: int,
) -> list[tuple[str, bool, str]]:
    """归档自检的判据（纯函数，不依赖 Spark，便于单测）。

    输入：
      kafka_count      本次从 topic 读到的消息数（0 表示 topic 已被 retention 清空）
      archived_total   归档表整表行数
      archived_touched 本次覆盖的 dt 分区在归档表里的行数
      parsed_count     本次成功解析出的行数

    返回 [(判据名, 是否通过, 说明)]。

    为什么不能拿"整表行数 == 本次消息数"：
      topic 一旦被 retention 清空，本次读到 0 条而归档表仍保留全量，
      等式永远不成立，归档任务每晚失败（历史上 24 次）。
    """
    out: list[tuple[str, bool, str]] = []
    if kafka_count == 0:
        out.append((
            "本次无新消息（topic 已被 retention 清空，归档保留历史）",
            True,
            f"归档现有 {archived_total:,} 行，保留不动",
        ))
    else:
        out.append((
            "本次覆盖分区行数 == Kafka 消息数",
            archived_touched == kafka_count,
            f"分区 {archived_touched:,} / Kafka {kafka_count:,}",
        ))
        out.append((
            "整表行数不少于本次消息数（归档是 topic 的超集）",
            archived_total >= kafka_count,
            f"整表 {archived_total:,} / 本次 {kafka_count:,}",
        ))
    out.append((
        "可解析行数 == Kafka 消息数",
        parsed_count == kafka_count,
        f"解析 {parsed_count:,} / Kafka {kafka_count:,}",
    ))
    return out
