"""一站式入口：先生成 MySQL 数据，再发送 Kafka 事件。

用法：
    python -m src

等价于依次执行：
    python -m src.generate_mysql_data
    python -m src.generate_events

注意：docs/sprint/SPRINT_0.md 规定的两个正式入口是
      src.generate_mysql_data 与 src.generate_events，
      本模块只是为了方便一次性跑完整链路。
"""

from __future__ import annotations

import sys

from . import generate_events, generate_mysql_data


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    print("=" * 60)
    print("步骤 1/2：生成 MySQL 业务数据")
    print("=" * 60)
    code = generate_mysql_data.main(args)
    if code != 0:
        return code

    print()
    print("=" * 60)
    print("步骤 2/2：发送 Kafka 事件")
    print("=" * 60)
    return generate_events.main(args)


if __name__ == "__main__":
    sys.exit(main())
