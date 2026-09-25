"""MySQL 连接与批量写入封装。

只使用 mysql-connector-python，不引入 ORM —— Sprint 0 需要的是
可读、可验证的显式 SQL，而不是抽象层。
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any

import mysql.connector
from mysql.connector import Error as MySQLError

from .common import get_logger
from .config import MySQLConfig

log = get_logger("db")

# 单条 INSERT 的最大语句长度控制：mysql-connector 会自动做
# 多值 INSERT，这里只控制每批行数，避免超过 max_allowed_packet。
DEFAULT_BATCH_SIZE = 1000


class MySQLClient:
    """MySQL 客户端，提供连接重试与批量插入。"""

    def __init__(self, config: MySQLConfig) -> None:
        self._config = config
        self._conn: mysql.connector.MySQLConnection | None = None

    # --------------------------------------------------------
    # 连接管理
    # --------------------------------------------------------
    def connect(self, retries: int = 30, interval: float = 2.0) -> None:
        """建立连接，失败时重试。

        容器刚启动时 MySQL 可能尚未就绪，因此必须重试。
        """
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self._conn = mysql.connector.connect(
                    host=self._config.host,
                    port=self._config.port,
                    database=self._config.database,
                    user=self._config.user,
                    password=self._config.password,
                    connection_timeout=self._config.connect_timeout,
                    autocommit=False,
                    charset="utf8mb4",
                    collation="utf8mb4_unicode_ci",
                )
                log.info("已连接 MySQL：%s", self._config.dsn())
                return
            except MySQLError as exc:
                last_error = exc
                if attempt % 5 == 1 or attempt == retries:
                    log.warning("连接 MySQL 失败（%d/%d）：%s", attempt, retries, exc)
                time.sleep(interval)

        raise RuntimeError(
            f"无法连接 MySQL {self._config.dsn()}，已重试 {retries} 次"
        ) from last_error

    @property
    def connection(self) -> mysql.connector.MySQLConnection:
        if self._conn is None or not self._conn.is_connected():
            raise RuntimeError("MySQL 连接尚未建立，请先调用 connect()")
        return self._conn

    def close(self) -> None:
        if self._conn is not None and self._conn.is_connected():
            self._conn.close()
            log.info("MySQL 连接已关闭")
        self._conn = None

    def __enter__(self) -> MySQLClient:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params or ())
            return list(cursor.fetchall())
        finally:
            cursor.close()

    def fetch_scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        rows = self.fetch_all(sql, params)
        if not rows:
            return None
        return rows[0][0]

    # --------------------------------------------------------
    # 批量写入
    # --------------------------------------------------------
    def insert_many(
        self,
        table: str,
        columns: Sequence[str],
        rows: Iterable[Sequence[Any]],
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> int:
        """批量插入。

        使用 executemany，由驱动生成多值 INSERT。
        """
        column_list = ", ".join(f"`{c}`" for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        sql = f"INSERT INTO `{table}` ({column_list}) VALUES ({placeholders})"

        cursor = self.connection.cursor()
        total = 0
        batch: list[Sequence[Any]] = []

        try:
            for row in rows:
                batch.append(row)
                if len(batch) >= batch_size:
                    cursor.executemany(sql, batch)
                    self.connection.commit()
                    total += len(batch)
                    log.info("  %s 已写入 %d 行", table, total)
                    batch.clear()

            if batch:
                cursor.executemany(sql, batch)
                self.connection.commit()
                total += len(batch)
                log.info("  %s 已写入 %d 行", table, total)
        except MySQLError:
            self.connection.rollback()
            raise
        finally:
            cursor.close()

        return total

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def table_exists(self, table: str) -> bool:
        value = self.fetch_scalar(
            """
            SELECT COUNT(*)
              FROM information_schema.tables
             WHERE table_schema = %s AND table_name = %s
            """,
            (self._config.database, table),
        )
        return bool(value)

    def count_rows(self, table: str) -> int:
        value = self.fetch_scalar(f"SELECT COUNT(*) FROM `{table}`")
        return int(value or 0)

    def truncate(self, tables: Sequence[str]) -> None:
        """清空表。

        注意：存在外键约束，必须按依赖顺序删除：
        先删子表（refund / payment / orders），再删父表（product / user）。
        """
        cursor = self.connection.cursor()
        try:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in tables:
                log.info("  清空表 %s", table)
                cursor.execute(f"TRUNCATE TABLE `{table}`")
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            self.connection.commit()
        except MySQLError:
            self.connection.rollback()
            raise
        finally:
            cursor.close()
