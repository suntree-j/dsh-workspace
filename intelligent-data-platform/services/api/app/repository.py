"""数据访问层：所有 SQL 常量与结果整形都集中在这里。

为什么把 SQL 单独放一层：
    1. SQL 是**服务自己写的常量**，不接受任何用户输入拼接；
       用户输入一律通过参数绑定（`%s` 占位符）传入；
    2. 集中放置便于逐条审查"这条查询读了哪些表、有没有 LIMIT、
       是否可能扫全表"，也便于将来迁移到别的查询引擎；
    3. 每个方法返回 (数据, 用到的表, 指标字段)，供接口层拼装 source 血缘信息。

约定：
    - 只查 `sqlguard.ALLOWED_TABLES` 里的表；
    - 每条语句都写 LIMIT（守卫还会再强制一次）；
    - 1 分钟窗口的可加指标"逐窗求和 = 全量口径"，
      因此 KPI 卡片直接对窗口表 SUM，不需要额外汇总表。
"""

from __future__ import annotations

from typing import Any

from .doris import DorisClient

__all__ = ["Repository", "BATCH_DATABASE"]

# 离线（批处理）指标所在库 —— 由 Sprint 3 的 load-batch-to-doris.sh 装载。
#
# 为什么单独一个库而不是塞进 ecommerce：
#   ecommerce 里是**实时链路**的表（Flink → Kafka → Routine Load）。
#   两套链路写同一个库、表名还高度相似，会让"这个数到底是实时的还是离线的"
#   变成需要靠记忆判断的事 —— 这正是数据口径事故的温床。
#   分库之后：库名即链路来源。
BATCH_DATABASE = "lakehouse_ads"


class Repository:
    """指标与明细的只读查询集合。"""

    def __init__(self, client: DorisClient) -> None:
        self.client = client

    # ========================================================
    # 总览
    # ========================================================
    def trade_kpi(self) -> tuple[dict[str, Any], list[str], list[str]]:
        """交易域全量 KPI（对所有 1 分钟窗口求和，即全量口径）。

        注意 `order_user_cnt` 不在这里算：窗口表的 `order_user_cnt` 是
        "每个窗口内的去重用户数"，**逐窗求和会变成人次，不是真 UV**。
        真去重值由 `distinct_users()` 从 DWD 明细算，见该方法注释。
        """
        sql = """
            SELECT
                SUM(gmv)                            AS gmv,
                SUM(order_cnt)                      AS order_cnt,
                SUM(payment_cnt)                    AS payment_cnt,
                SUM(payment_amount)                 AS payment_amount,
                SUM(payment_fail_cnt)               AS payment_fail_cnt,
                SUM(refund_cnt)                     AS refund_cnt,
                SUM(refund_amount)                  AS refund_amount,
                CAST(SUM(gmv) / NULLIF(SUM(order_cnt), 0) AS DECIMAL(18, 2))            AS avg_order_amount,
                CAST(SUM(payment_cnt) / NULLIF(SUM(payment_cnt) + SUM(payment_fail_cnt), 0)
                     AS DECIMAL(10, 4))                                                 AS payment_success_rate,
                CAST(SUM(refund_amount) / NULLIF(SUM(payment_amount), 0)
                     AS DECIMAL(10, 4))                                                 AS refund_rate
            FROM ads_realtime_trade_1m
        """
        fields = [
            "gmv", "order_cnt", "avg_order_amount",
            "payment_cnt", "payment_amount", "payment_fail_cnt", "payment_success_rate",
            "refund_cnt", "refund_amount", "refund_rate",
        ]
        row = self.client.query(sql, max_limit=1).first
        return row, ["ecommerce.ads_realtime_trade_1m"], fields

    def traffic_kpi(self) -> tuple[dict[str, Any], list[str], list[str]]:
        """流量域全量 KPI（PV 与各行为计数为可加指标，逐窗求和即全量）。

        `uv` 同样不能用逐窗求和：那是"人次"。真 UV 见 `distinct_users()`。
        """
        sql = """
            SELECT
                SUM(pv)             AS pv,
                SUM(view_cnt)       AS view_cnt,
                SUM(click_cnt)      AS click_cnt,
                SUM(cart_cnt)       AS cart_cnt,
                SUM(favorite_cnt)   AS favorite_cnt,
                SUM(buy_cnt)        AS buy_cnt,
                CAST(SUM(click_cnt) / NULLIF(SUM(view_cnt), 0) AS DECIMAL(10, 4))   AS click_rate,
                CAST(SUM(cart_cnt) / NULLIF(SUM(click_cnt), 0) AS DECIMAL(10, 4))   AS cart_rate,
                CAST(SUM(buy_cnt) / NULLIF(SUM(cart_cnt), 0)   AS DECIMAL(10, 4))   AS buy_rate
            FROM ads_realtime_traffic_1m
        """
        fields = [
            "pv", "view_cnt", "click_cnt", "cart_cnt", "favorite_cnt", "buy_cnt",
            "click_rate", "cart_rate", "buy_rate",
        ]
        row = self.client.query(sql, max_limit=1).first
        return row, ["ecommerce.ads_realtime_traffic_1m"], fields

    def distinct_users(self) -> tuple[dict[str, Any], list[str], list[str]]:
        """全量去重用户数（真 UV）—— 必须回到 DWD 明细去重。

        为什么不能在窗口表上 SUM：
            窗口表的 `uv` / `order_user_cnt` 是**每个窗口内**的去重值，
            同一个用户出现在 10 个窗口就会被数 10 次。
            想要"有多少个用户下过单/来过"，只能对明细做 COUNT(DISTINCT user_id)。
        """
        order_users = self.client.query(
            "SELECT COUNT(DISTINCT user_id) AS order_user_cnt FROM dwd_trade_order_detail",
            max_limit=1,
        ).first
        behavior_users = self.client.query(
            "SELECT COUNT(DISTINCT user_id) AS uv FROM dwd_traffic_behavior_detail",
            max_limit=1,
        ).first

        data = {
            "order_user_cnt": order_users.get("order_user_cnt"),
            "uv": behavior_users.get("uv"),
        }
        tables = [
            "ecommerce.dwd_trade_order_detail",
            "ecommerce.dwd_traffic_behavior_detail",
        ]
        return data, tables, ["order_user_cnt", "uv"]

    def window_stats(self) -> tuple[dict[str, Any], list[str]]:
        """各指标表的窗口数与窗口时间范围（用于判断链路是否新鲜）。"""
        trade = self.client.query(
            "SELECT COUNT(*) AS windows, MIN(window_start) AS earliest, MAX(window_start) AS latest "
            "FROM ads_realtime_trade_1m",
            max_limit=1,
        ).first
        traffic = self.client.query(
            "SELECT COUNT(*) AS windows FROM ads_realtime_traffic_1m", max_limit=1
        ).first
        category = self.client.query(
            "SELECT COUNT(*) AS windows FROM ads_realtime_category_1m", max_limit=1
        ).first
        data = {
            "trade": trade.get("windows"),
            "traffic": traffic.get("windows"),
            "category": category.get("windows"),
            "earliest_window": trade.get("earliest"),
            "latest_window": trade.get("latest"),
        }
        tables = [
            "ecommerce.ads_realtime_trade_1m",
            "ecommerce.ads_realtime_traffic_1m",
            "ecommerce.ads_realtime_category_1m",
        ]
        return data, tables

    def category_top(self, limit: int = 5, window_limit: int | None = None) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """类目销售排行（默认全量口径）。"""
        if window_limit:
            sql = f"""
                SELECT category_name,
                       SUM(order_cnt)      AS order_cnt,
                       SUM(gmv)            AS gmv,
                       SUM(total_quantity) AS total_quantity,
                       CAST(SUM(gmv) / NULLIF(SUM(order_cnt), 0) AS DECIMAL(18, 2)) AS avg_order_amount
                FROM ads_realtime_category_1m
                WHERE window_start >= DATE_SUB(
                    (SELECT MAX(window_start) FROM ads_realtime_category_1m),
                    INTERVAL {int(window_limit)} MINUTE)
                GROUP BY category_name
                ORDER BY gmv DESC
                LIMIT {int(limit)}
            """
        else:
            sql = f"""
                SELECT category_name,
                       SUM(order_cnt)      AS order_cnt,
                       SUM(gmv)            AS gmv,
                       SUM(total_quantity) AS total_quantity,
                       CAST(SUM(gmv) / NULLIF(SUM(order_cnt), 0) AS DECIMAL(18, 2)) AS avg_order_amount
                FROM ads_realtime_category_1m
                GROUP BY category_name
                ORDER BY gmv DESC
                LIMIT {int(limit)}
            """
        fields = ["order_cnt", "gmv", "total_quantity", "avg_order_amount"]
        return self.client.query(sql).rows, ["ecommerce.ads_realtime_category_1m"], fields

    # ========================================================
    # 时间序列
    # ========================================================
    def trade_series(self, limit: int) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """最近 N 个窗口的交易指标（升序返回，便于直接画折线）。"""
        sql = f"""
            SELECT window_start, window_end, gmv, order_cnt, order_user_cnt, avg_order_amount,
                   payment_cnt, payment_amount, payment_fail_cnt, payment_success_rate,
                   refund_cnt, refund_amount, refund_rate
            FROM (
                SELECT window_start, window_end, gmv, order_cnt, order_user_cnt, avg_order_amount,
                       payment_cnt, payment_amount, payment_fail_cnt, payment_success_rate,
                       refund_cnt, refund_amount, refund_rate
                FROM ads_realtime_trade_1m
                ORDER BY window_start DESC
                LIMIT {int(limit)}
            ) recent
            ORDER BY window_start
        """
        fields = [
            "gmv", "order_cnt", "order_user_cnt", "avg_order_amount", "payment_cnt",
            "payment_amount", "payment_fail_cnt", "payment_success_rate",
            "refund_cnt", "refund_amount", "refund_rate",
        ]
        return self.client.query(sql).rows, ["ecommerce.ads_realtime_trade_1m"], fields

    def traffic_series(self, limit: int) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """最近 N 个窗口的流量指标（升序）。"""
        sql = f"""
            SELECT window_start, window_end, uv, pv, view_cnt, click_cnt, cart_cnt,
                   favorite_cnt, buy_cnt, click_rate, cart_rate, buy_rate
            FROM (
                SELECT window_start, window_end, uv, pv, view_cnt, click_cnt, cart_cnt,
                       favorite_cnt, buy_cnt, click_rate, cart_rate, buy_rate
                FROM ads_realtime_traffic_1m
                ORDER BY window_start DESC
                LIMIT {int(limit)}
            ) recent
            ORDER BY window_start
        """
        fields = ["uv", "pv", "view_cnt", "click_cnt", "cart_cnt", "favorite_cnt", "buy_cnt",
                  "click_rate", "cart_rate", "buy_rate"]
        return self.client.query(sql).rows, ["ecommerce.ads_realtime_traffic_1m"], fields

    # ========================================================
    # 漏斗
    # ========================================================
    def funnel(self, window_limit: int) -> tuple[dict[str, Any], list[str], list[str]]:
        """行为漏斗：浏览 → 点击 → 加购 → 购买。"""
        sql = f"""
            SELECT SUM(view_cnt)  AS view_cnt,
                   SUM(click_cnt) AS click_cnt,
                   SUM(cart_cnt)  AS cart_cnt,
                   SUM(buy_cnt)   AS buy_cnt,
                   CAST(SUM(click_cnt) / NULLIF(SUM(view_cnt), 0) AS DECIMAL(10, 4)) AS click_rate,
                   CAST(SUM(cart_cnt) / NULLIF(SUM(click_cnt), 0) AS DECIMAL(10, 4)) AS cart_rate,
                   CAST(SUM(buy_cnt) / NULLIF(SUM(cart_cnt), 0)   AS DECIMAL(10, 4)) AS buy_rate
            FROM dws_traffic_overview_1m
            WHERE window_start >= DATE_SUB(
                (SELECT MAX(window_start) FROM dws_traffic_overview_1m),
                INTERVAL {int(window_limit)} MINUTE)
        """
        row = self.client.query(sql, max_limit=1).first
        fields = ["view_cnt", "click_cnt", "cart_cnt", "buy_cnt", "click_rate", "cart_rate", "buy_rate"]
        return row, ["ecommerce.dws_traffic_overview_1m"], fields

    # ========================================================
    # 订单明细
    # ========================================================
    def orders(
        self,
        limit: int,
        offset: int,
        category: str | None,
        start: str | None,
        end: str | None,
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """订单明细分页查询（条件下推，参数绑定）。"""
        where, params = self._order_filters(category, start, end)

        total = self.client.query(
            f"SELECT COUNT(*) AS total FROM dwd_trade_order_detail {where}",
            params,
            max_limit=1,
        ).first.get("total")

        rows = self.client.query(
            f"""
            SELECT order_id, user_id, product_id, category_name, user_level, province,
                   quantity, amount, event_time
            FROM dwd_trade_order_detail
            {where}
            ORDER BY event_time DESC, order_id DESC
            LIMIT {int(limit)} OFFSET {int(offset)}
            """,
            params,
        ).rows

        data = {"total": total, "limit": limit, "offset": offset, "items": rows}
        return data, ["ecommerce.dwd_trade_order_detail"], ["amount"]

    def order_detail(self, order_id: int) -> tuple[dict[str, Any] | None, list[str], list[str]]:
        """单笔订单及其支付、退款记录（展示跨表查询能力）。"""
        order = self.client.query(
            """
            SELECT order_id, user_id, product_id, category_name, user_level, province,
                   quantity, amount, event_id, event_time, dt
            FROM dwd_trade_order_detail
            WHERE order_id = %s
            """,
            (order_id,),
            max_limit=1,
        ).first
        if not order:
            return None, [], []

        payments = self.client.query(
            """
            SELECT payment_id, amount, payment_method, payment_status, event_time
            FROM dwd_trade_payment_detail
            WHERE order_id = %s
            ORDER BY event_time
            """,
            (order_id,),
            max_limit=20,
        ).rows

        refunds = self.client.query(
            """
            SELECT refund_id, refund_amount, event_time
            FROM dwd_trade_refund_detail
            WHERE order_id = %s
            ORDER BY event_time
            """,
            (order_id,),
            max_limit=20,
        ).rows

        tables = [
            "ecommerce.dwd_trade_order_detail",
            "ecommerce.dwd_trade_payment_detail",
            "ecommerce.dwd_trade_refund_detail",
        ]
        return {"order": order, "payments": payments, "refunds": refunds}, tables, ["amount"]

    @staticmethod
    def _order_filters(
        category: str | None, start: str | None, end: str | None
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category_name = %s")
            params.append(category)
        if start:
            clauses.append("event_time >= %s")
            params.append(f"{start} 00:00:00")
        if end:
            clauses.append("event_time < %s")
            params.append(f"{end} 23:59:59")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # ========================================================
    # 离线链路（Sprint 3）：批处理算出来的同名同口径指标
    #
    # 与上面的实时方法一一对应，**公式逐字相同** —— 这正是"同名同口径"的落地方式：
    #   实时 trade_kpi()        ↔  离线 batch_trade_kpi()
    #   实时 trade_series()     ↔  离线 batch_trade_series()
    #   实时 category_top()     ↔  离线 batch_category_top()
    # 如果哪天有人"顺手优化"了其中一侧的公式，对账（reconcile）会立刻报警，
    # 因为 lakehouse.ads_reconcile_trade_1m 会记下每一分钟的差异。
    # ========================================================
    def batch_trade_kpi(self) -> tuple[dict[str, Any], list[str], list[str]]:
        """离线链路交易域全量 KPI（与 trade_kpi 同公式、同粒度）。"""
        sql = f"""
            SELECT
                SUM(gmv)                            AS gmv,
                SUM(order_cnt)                      AS order_cnt,
                SUM(payment_cnt)                    AS payment_cnt,
                SUM(payment_amount)                 AS payment_amount,
                SUM(payment_fail_cnt)               AS payment_fail_cnt,
                SUM(refund_cnt)                     AS refund_cnt,
                SUM(refund_amount)                  AS refund_amount,
                CAST(SUM(gmv) / NULLIF(SUM(order_cnt), 0) AS DECIMAL(18, 2))            AS avg_order_amount,
                CAST(SUM(payment_cnt) / NULLIF(SUM(payment_cnt) + SUM(payment_fail_cnt), 0)
                     AS DECIMAL(10, 4))                                                 AS payment_success_rate,
                CAST(SUM(refund_amount) / NULLIF(SUM(payment_amount), 0)
                     AS DECIMAL(10, 4))                                                 AS refund_rate
            FROM {BATCH_DATABASE}.ads_batch_trade_1m
        """
        fields = [
            "gmv", "order_cnt", "avg_order_amount",
            "payment_cnt", "payment_amount", "payment_fail_cnt", "payment_success_rate",
            "refund_cnt", "refund_amount", "refund_rate",
        ]
        row = self.client.query(sql, max_limit=1).first
        return row, [f"{BATCH_DATABASE}.ads_batch_trade_1m"], fields

    def batch_trade_daily(self, limit: int = 60) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """离线链路按天指标（报表口径，取最近 N 天，按日期升序）。

        为什么看板用天表而不是分钟表：
            离线数据的价值是"准确、可回溯"，不是"秒级"。
            让看板把 11000+ 行分钟数据拉回来自己聚合，
            既浪费带宽也把口径计算搬到了前端 —— 天表就是为此存在的。
        """
        sql = f"""
            SELECT dt, gmv, order_cnt, order_user_cnt, avg_order_amount,
                   payment_cnt, payment_amount, payment_fail_cnt, payment_success_rate,
                   refund_cnt, refund_amount, refund_rate
            FROM (
                SELECT dt, gmv, order_cnt, order_user_cnt, avg_order_amount,
                       payment_cnt, payment_amount, payment_fail_cnt, payment_success_rate,
                       refund_cnt, refund_amount, refund_rate
                FROM {BATCH_DATABASE}.ads_batch_trade_1d
                ORDER BY dt DESC
                LIMIT {int(limit)}
            ) recent
            ORDER BY dt
        """
        fields = [
            "gmv", "order_cnt", "order_user_cnt", "avg_order_amount", "payment_cnt",
            "payment_amount", "payment_fail_cnt", "payment_success_rate",
            "refund_cnt", "refund_amount", "refund_rate",
        ]
        return self.client.query(sql).rows, [f"{BATCH_DATABASE}.ads_batch_trade_1d"], fields

    def batch_category_top(self, limit: int = 10) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        """离线链路类目销售排行（全量口径，与 category_top 同公式）。"""
        sql = f"""
            SELECT category_name,
                   SUM(order_cnt)      AS order_cnt,
                   SUM(gmv)            AS gmv,
                   SUM(total_quantity) AS total_quantity,
                   CAST(SUM(gmv) / NULLIF(SUM(order_cnt), 0) AS DECIMAL(18, 2)) AS avg_order_amount
            FROM {BATCH_DATABASE}.ads_batch_category_1m
            GROUP BY category_name
            ORDER BY gmv DESC
            LIMIT {int(limit)}
        """
        fields = ["order_cnt", "gmv", "total_quantity", "avg_order_amount"]
        return self.client.query(sql).rows, [f"{BATCH_DATABASE}.ads_batch_category_1m"], fields

    def reconciliation(self) -> tuple[dict[str, Any], list[str], list[str]]:
        """批流对账结论（最近一批）+ 实时/离线关键指标并排对比。

        这是"数据可信"的对外证据：
            latest   —— 最近一次对账的区间、窗口数、不一致数、GMV 对比
            mismatch —— 若存在差异，给出差异最大的若干窗口（便于定位）
        服务层只读，不参与对账本身（对账是 Spark 作业完成的）。
        """
        latest = self.client.query(
            f"""
            SELECT batch_id, compared_at, scope_start, scope_end,
                   realtime_windows, batch_windows, matched_windows, mismatched_windows,
                   first_mismatch_at, realtime_total_gmv, batch_total_gmv, is_pass
            FROM {BATCH_DATABASE}.ads_reconcile_summary
            ORDER BY compared_at DESC
            LIMIT 1
            """,
            max_limit=1,
        ).first

        mismatches = self.client.query(
            f"""
            SELECT window_start, diff_gmv, diff_order_cnt, diff_order_user_cnt,
                   diff_payment_cnt, diff_payment_amount, diff_payment_fail_cnt,
                   diff_refund_cnt, diff_refund_amount
            FROM {BATCH_DATABASE}.ads_reconcile_trade_1m
            WHERE is_match = false
            ORDER BY window_start
            LIMIT 20
            """
        ).rows

        realtime = self.client.query(
            "SELECT SUM(gmv) AS gmv, SUM(order_cnt) AS order_cnt, "
            "SUM(payment_amount) AS payment_amount, SUM(refund_amount) AS refund_amount "
            "FROM ads_realtime_trade_1m",
            max_limit=1,
        ).first
        batch = self.client.query(
            f"SELECT SUM(gmv) AS gmv, SUM(order_cnt) AS order_cnt, "
            f"SUM(payment_amount) AS payment_amount, SUM(refund_amount) AS refund_amount "
            f"FROM {BATCH_DATABASE}.ads_batch_trade_1m",
            max_limit=1,
        ).first

        data = {
            "latest": latest or {},
            "mismatches": mismatches,
            "mismatch_count_shown": len(mismatches),
            "totals": {
                "realtime": realtime or {},
                "batch": batch or {},
            },
        }
        tables = [
            f"{BATCH_DATABASE}.ads_reconcile_summary",
            f"{BATCH_DATABASE}.ads_reconcile_trade_1m",
            f"{BATCH_DATABASE}.ads_batch_trade_1m",
            "ecommerce.ads_realtime_trade_1m",
        ]
        fields = ["gmv", "order_cnt", "payment_amount", "refund_amount"]
        return data, tables, fields

    # ========================================================
    # 动态只读查询（Sprint 7：给 Agent 用）
    # ========================================================
    def sql_query(self, sql: str, max_limit: int) -> tuple[dict[str, Any], list[str], str]:
        """执行一条**外部传入**的 SELECT（唯一允许外部 SQL 的入口）。

        安全边界（三道，缺一不可）：
            1. `DorisClient.query` 内部第一件事就是 `sqlguard.validate_select`，
               只放行 SELECT、拒绝多语句/注释/DDL/DML、表白名单、强制 LIMIT；
            2. 连接用的是**只读账号** `agent_ro`，即使守卫被绕过，Doris 也会拒绝写；
            3. 连接带 read_timeout，慢查询会超时断开而不是拖垮集群。

        !! 为什么必须返回"实际执行的 SQL" !!
            `Agent 不得伪造查询结果`（AGENTS.md 第 10.1 节）的前提是
            **能证明这段 SQL 真的被跑过**。守卫会改写语句（补/收 LIMIT），
            因此返回的必须是改写后的最终语句，而不是调用方传进来的那个字符串 ——
            否则"我查了 X"这句话本身就不成立。

        表名从**实际执行的 SQL** 里提取，而不是让调用方自报：
            以后写进审计日志时，血缘信息才不会因为调用方说谎而失真。
        """
        from .sqlguard import extract_tables  # 放在函数内，避免模块级循环导入

        result = self.client.query(sql, max_limit=max_limit)
        tables = extract_tables(result.sql)
        data = {
            "rows": result.rows,
            "row_count": len(result.rows),
            "elapsed_ms": result.elapsed_ms,
            "executed_sql": result.sql,
        }
        return data, tables, result.sql

    # ========================================================
    # 元数据
    # ========================================================
    def categories(self) -> tuple[list[str], list[str]]:
        """可选类目列表（用于前端下拉筛选）。"""
        rows = self.client.query(
            "SELECT DISTINCT category_name FROM ads_realtime_category_1m "
            "WHERE category_name IS NOT NULL ORDER BY category_name",
            max_limit=100,
        ).rows
        return [r["category_name"] for r in rows], ["ecommerce.ads_realtime_category_1m"]

    def table_metadata(self, tables: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
        """表白名单的字段结构（来自 information_schema，只读）。

        注意要查**两个库**：
            实时链路的表在 ecommerce，离线链路的表在 lakehouse_ads。
            只按 ecommerce 过滤会让 /meta/tables 里永远看不到离线表，
            并且是静默的"少了几个表"（不报错），很难被发现。
        """
        placeholders = ", ".join(["%s"] * len(tables))
        schemas = ["ecommerce", BATCH_DATABASE]
        schema_placeholders = ", ".join(["%s"] * len(schemas))

        columns = self.client.query(
            f"""
            SELECT table_schema, table_name, column_name, data_type, column_comment, column_type
            FROM information_schema.columns
            WHERE table_schema IN ({schema_placeholders}) AND table_name IN ({placeholders})
            ORDER BY table_name, ordinal_position
            """,
            [*schemas, *tables],
            max_limit=500,
        ).rows
        comments = self.client.query(
            f"""
            SELECT table_schema, table_name, table_comment
            FROM information_schema.tables
            WHERE table_schema IN ({schema_placeholders}) AND table_name IN ({placeholders})
            """,
            [*schemas, *tables],
            max_limit=200,
        ).rows

        comment_map = {r["table_name"]: r["table_comment"] for r in comments}
        schema_map = {r["table_name"]: r["table_schema"] for r in comments}
        grouped: dict[str, dict[str, Any]] = {}
        for row in columns:
            name = row["table_name"]
            entry = grouped.setdefault(
                name,
                {
                    "table": name,
                    "database": schema_map.get(name, "ecommerce"),
                    "layer": _layer_of(name),
                    "comment": comment_map.get(name, ""),
                    "columns": [],
                },
            )
            entry["columns"].append(
                {
                    "name": row["column_name"],
                    "type": row["column_type"] or row["data_type"],
                    "comment": row["column_comment"] or "",
                }
            )

        ordered = [grouped[name] for name in tables if name in grouped]
        return ordered, ["information_schema.columns", "information_schema.tables"]


def _layer_of(table: str) -> str:
    """按表名前缀判断数仓分层。"""
    for prefix, layer in (("dwd_", "DWD"), ("dws_", "DWS"), ("ads_", "ADS"), ("dim_", "维表")):
        if table.startswith(prefix):
            return layer
    return "其他"
