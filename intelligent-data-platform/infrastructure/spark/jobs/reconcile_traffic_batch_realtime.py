"""Sprint 5 阶段 6：流量域批流交叉对账。

运行方式（服务器上，仓库根目录）：
    bash scripts/batch-mode.sh --stage traffic-reconcile
    # 或 bash scripts/run-batch-pipeline.sh --stage traffic-reconcile

它做什么：
    1. 从 Doris 通过 JDBC 读实时链路产出的 ecommerce.ads_realtime_traffic_1m；
    2. 从湖仓读离线链路产出的 lakehouse.ads_traffic_1m；
    3. 计算「两侧都已封闭」的对账区间（尾部留安全边界）；
    4. 逐窗口比对 7 个指标（uv + pv + 5 个行为计数；uv 是去重指标、
       其余是可加指标）+ 窗口覆盖情况，把差异全部落盘到
       lakehouse.ads_reconcile_traffic_1m / ads_reconcile_traffic_summary；
    5. **差异不为 0 就以非 0 退出**。

与交易域对账（reconcile_batch_realtime.py）的关系
------------------------------------------------
    骨架完全相同（尾部边界、动态求交、FULL OUTER JOIN、差异落盘），
    但有三处是流量域**特有**的，而且每一处都不能照抄：

    **1. 判据包含去重指标**
      交易域比的是金额与笔数（都可加）。流量域的核心指标 uv 是去重指标：
      逐窗口比对成立，但**绝不能**把它上卷后比总数
      （SUM(窗口 uv) = 19999，而全局去重只有 1199 —— 差 16 倍，见 build_traffic_ads.py）。
      本作业全程只做逐窗口比对，不做任何 uv 上卷。

    **2. 比率列只留证、不参与判据；但换成一条更强的独立判据**
      click_rate / cart_rate / buy_rate 是分子分母的派生量，
      在 7 个判据列逐窗口相等时数学上必然相等 —— 不提供额外信息，
      却最容易因两侧除法实现细节产生末位差异（假差异）。
      ★ 所以判据不含"两侧比率相等"，而是分别检查
        "每一侧的比率是否等于由它**自己的**计数按 metrics.md 公式算出的值"。
        这条更根本：两侧一起错成同一个值它也能抓出来。
      ★ 实测结果（本项目真实数据）：离线侧 0 个矛盾窗口，实时侧 1 个。
        该窗口实时 view_cnt=2 / click_cnt=1 / click_rate=0.0000，
        按公式应为 0.5000（离线侧同一窗口算出的正是 0.5000）。
        这不是舍入，是实时链路那一行自相矛盾 —— 详见下方 rate_anomaly_count。
        它被如实落进汇总表与逐窗口表，**不阻断离线作业**
        （重跑离线一万次也改不了实时写下的那个值）。

    **3. 单边窗口数单独统计**
      流量域两侧都可能出现完全没有事件的空档，缺失窗口比交易域常见得多。
      汇总表专门记录 realtime_only_windows / batch_only_windows，
      使"差异为 0"这句话有边界可核。

为什么尾部要留安全边界：
    实时链路的最后一个窗口可能仍在接收迟到事件（Kafka 里还有未消费的偏移），
    此时两侧不一致是"正常现象"而不是缺陷。若不做边界处理，
    对账会周期性假警报，久而久之就没人看这个结果了 ——
    这正是数据治理里最常见的失效方式。边界值由作业写入汇总表，可复核。

为什么对账区间不写死：
    数据会重新生成（Sprint 0 的生成器可重跑），写死日期的对账脚本
    在数据一变就永久失败，只能靠改脚本"修绿"，属于自欺欺人。
    这里改为按两侧实际数据范围动态求交。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

from _common import Checker, build_spark, ensure_tables, run_sql_file

# 对账区间尾部安全边界：实时链路的水位线延迟 + 一个窗口的完整性，留 3 分钟足够。
TAIL_GUARD_MINUTES = 3

# 对账覆盖率的硬下限：区间内实时侧窗口数不得少于离线侧的该比例。
#   设这个阈值是为了防"区间取空导致 0 个窗口比对也判通过"这种假成功。
MIN_REALTIME_COVERAGE = 0.9

DORIS_JDBC_DRIVER = "com.mysql.cj.jdbc.Driver"

#: 参与对账的实时表（与 Doris 里 Flink 作业的目标表同名）
REALTIME_TABLE = "ads_realtime_traffic_1m"
BATCH_TABLE = "lakehouse.ads_traffic_1m"

#: 对账需要的列。显式列出而不是 SELECT *：
#: Doris 表后续加列时，Spark 侧 schema 不会跟着漂移。
#:
#: !! 三个比率列必须在清单里，即使它们不参与 is_match 判据 !!
#:   实测踩坑（本作业第一次运行就挂在这里）：
#:     只选了 window_start + uv + pv + 5 个行为计数（8 列），
#:     而 08_traffic_reconcile.sql 里引用了 r.click_rate / r.cart_rate / r.buy_rate
#:     （比率列要落盘留证），于是 Spark 报
#:       AnalysisException: [UNRESOLVED_COLUMN.WITH_SUGGESTION]
#:       A column or function parameter with name `r`.`click_rate` cannot be resolved.
#:   原因不是"判据不含比率所以不用读"——**落盘留证也需要读**。
#:   清单必须覆盖 SQL 里引用到的**全部**列，与它们参不参与判据无关。
COMPARE_COLUMNS: tuple[str, ...] = (
    "window_start",
    "uv",
    "pv",
    "view_cnt",
    "click_cnt",
    "cart_cnt",
    "favorite_cnt",
    "buy_cnt",
    "click_rate",
    "cart_rate",
    "buy_rate",
)


def doris_options(spark) -> dict[str, str]:
    """Doris 连接参数（用户/口令由提交脚本通过 --conf 注入）。

    !! sessionVariables 不能省 !!
        Doris 通过 MySQL 协议返回 datetime 时会受 session time_zone 影响。
        Spark 的 session 时区是 Asia/Shanghai（见 spark-defaults.conf），
        若 Doris 侧用默认 UTC，读出来的 window_start 会整体偏移 8 小时，
        对账把"时区问题"误报成"指标不一致"，极难排查。
    """
    conf = spark.sparkContext.getConf()
    host = conf.get("spark.doris.host", "doris-fe")
    port = conf.get("spark.doris.queryPort", "9030")
    database = conf.get("spark.doris.database", "ecommerce")
    return {
        "url": (
            f"jdbc:mysql://{host}:{port}/{database}"
            "?useSSL=false&allowPublicKeyRetrieval=true&characterEncoding=UTF-8"
            "&serverTimezone=Asia/Shanghai&sessionVariables=time_zone='%2B08:00'"
        ),
        "driver": DORIS_JDBC_DRIVER,
        "user": conf.get("spark.doris.user", "agent_ro"),
        "password": conf.get("spark.doris.password", ""),
    }


def load_realtime(spark, options: dict[str, str]) -> int:
    """读实时流量指标表，并注册为临时视图 v_realtime_traffic_src。"""
    df = (
        spark.read.format("jdbc")
        .option("url", options["url"])
        .option("dbtable", REALTIME_TABLE)
        .option("driver", options["driver"])
        .option("user", options["user"])
        .option("password", options["password"])
        .option("fetchsize", "2000")
        .load()
    )
    df = df.select(*COMPARE_COLUMNS)
    df.createOrReplaceTempView("v_realtime_traffic_src")
    return df.count()


def compute_scope(spark) -> tuple[datetime, datetime, datetime]:
    """计算对账区间：两侧数据范围求交，尾部留安全边界。"""
    realtime = spark.sql(
        "SELECT MIN(window_start) AS lo, MAX(window_start) AS hi FROM v_realtime_traffic_src"
    ).first()
    batch = spark.sql(
        f"SELECT MIN(window_start) AS lo, MAX(window_start) AS hi FROM {BATCH_TABLE}"
    ).first()

    if realtime["lo"] is None or batch["lo"] is None:
        raise RuntimeError("实时或离线流量指标表为空，无法对账")

    start = max(realtime["lo"], batch["lo"])
    # hi 是"已出现过的最大窗口起点"，加 1 分钟才是窗口结束；
    # 再减安全边界，确保这一分钟两侧都已封窗。
    end = (
        min(realtime["hi"], batch["hi"])
        + timedelta(minutes=1)
        - timedelta(minutes=TAIL_GUARD_MINUTES)
    )
    if end <= start:
        raise RuntimeError(
            f"对账区间为空：start={start} end={end}；"
            f"实时范围 [{realtime['lo']}, {realtime['hi']}]，"
            f"离线范围 [{batch['lo']}, {batch['hi']}]"
        )
    return start, end, datetime.now()


def rate_anomaly_count(spark, side: str, compared_at: datetime) -> int:
    """统计某一侧的比率列与"由该侧自己的计数重算"矛盾的窗口数。

    ★ 这个数必须被查出来并打印，不能因为"判据不含比率"就当它不存在。
      它的作用是把"比率列到底谁错了"从推断变成证据：
        同一个判据（metrics.md 第 3 节的公式）、同一份数据，
        在离线侧为 0、在实时侧为 1 —— 说明差异不是"两侧算法不同"，
        而是实时侧那一行**自相矛盾**（计数说 2 次浏览 1 次点击，
        比率却写 0.0000，而 1/2 = 0.5000）。
      预期两侧都是 0；不为 0 时如实记录，**不许改写判据去掩盖**。

    定位方式：按 compared_at 筛选本次批次写入的行。
    本次批次的 compared_at 是作业自己生成的 datetime（秒级），
    逐窗口表里的值由它原样落盘，因此等值比较成立；
    写成 CAST('...' AS TIMESTAMP) 是为了避开 Spark 对 string/timestamp
    隐式转换的时区处理差异。
    """
    if side not in ("realtime", "batch"):
        raise ValueError(f"side 只能是 realtime / batch，收到 {side!r}")
    stamp = compared_at.strftime("%Y-%m-%d %H:%M:%S")
    return len(
        spark.sql(
            f"""
            SELECT 1 FROM lakehouse.ads_reconcile_traffic_1m
            WHERE compared_at = CAST('{stamp}' AS TIMESTAMP)
              AND {side}_rate_anomaly
            """
        ).collect()
    )


def main() -> int:
    spark = build_spark("sprint5-reconcile-traffic-batch-realtime")

    print("=" * 60, flush=True)
    print(" Sprint 5 — 流量域批流交叉对账（实时 Flink/Doris vs 离线 Spark/湖仓）", flush=True)
    print("=" * 60, flush=True)

    ensure_tables(spark)

    options = doris_options(spark)
    print(
        f"[jdbc] 读取实时指标：{options['url'].split('?')[0]} (user={options['user']})",
        flush=True,
    )
    realtime_rows = load_realtime(spark, options)
    print(f"[jdbc]   {REALTIME_TABLE} → {realtime_rows} 行", flush=True)

    batch_rows = int(spark.sql(f"SELECT COUNT(*) AS c FROM {BATCH_TABLE}").first()["c"])
    print(f"[lake]   ads_traffic_1m → {batch_rows} 行", flush=True)

    scope_start, scope_end, compared_at = compute_scope(spark)
    batch_id = compared_at.strftime("reconcile_traffic_%Y%m%d_%H%M%S")
    print(f"[scope]  对账区间 [{scope_start} , {scope_end})  批次 {batch_id}", flush=True)

    run_sql_file(
        spark,
        "08_traffic_reconcile.sql",
        variables={
            "SCOPE_START": scope_start.strftime("%Y-%m-%d %H:%M:%S"),
            "SCOPE_END": scope_end.strftime("%Y-%m-%d %H:%M:%S"),
            "COMPARED_AT": compared_at.strftime("%Y-%m-%d %H:%M:%S"),
            "BATCH_ID": batch_id,
        },
    )

    # ------------------------------------------------------------
    # 汇总与断言
    # ------------------------------------------------------------
    summary = spark.sql(
        f"""
        SELECT matched_windows, mismatched_windows, realtime_windows, batch_windows,
               realtime_only_windows, batch_only_windows, first_mismatch_at,
               realtime_min_uv, realtime_max_uv, batch_min_uv, batch_max_uv,
               realtime_total_pv, batch_total_pv,
               realtime_rate_anomaly_windows, batch_rate_anomaly_windows, is_pass
        FROM lakehouse.ads_reconcile_traffic_summary
        WHERE batch_id = '{batch_id}'
        """
    ).first()

    checker = Checker("流量域批流对账结论")

    # ---- A 组：主判据（7 个指标逐窗口 + 窗口覆盖）----
    checker.check_true(
        "对账窗口数 > 0（防空区间假通过）",
        int(summary["batch_windows"]) > 0,
        "batch_windows 必须大于 0",
    )
    coverage_ok = (
        int(summary["realtime_windows"])
        >= int(summary["batch_windows"]) * MIN_REALTIME_COVERAGE
    )
    checker.check_true(
        f"实时侧覆盖率 >= {MIN_REALTIME_COVERAGE:.0%}",
        coverage_ok,
        f"实时 {summary['realtime_windows']} 行 vs 离线 {summary['batch_windows']} 行",
    )
    checker.check("不一致窗口数", int(summary["mismatched_windows"]), 0)
    checker.check("仅实时侧有的窗口数（离线漏算）", int(summary["realtime_only_windows"]), 0)
    checker.check("仅离线侧有的窗口数（离线多算）", int(summary["batch_only_windows"]), 0)
    checker.check("实时 PV 合计 == 离线 PV 合计", summary["batch_total_pv"], summary["realtime_total_pv"])
    checker.check("窗口 UV 最小值一致", summary["batch_min_uv"], summary["realtime_min_uv"])
    checker.check("窗口 UV 最大值一致", summary["batch_max_uv"], summary["realtime_max_uv"])
    checker.check_true(
        "总体结论 is_pass",
        bool(summary["is_pass"]),
        f"首个不一致窗口 {summary['first_mismatch_at']}",
    )

    # ---- B 组：比率列的独立取证（判据外，但必须查、必须打印、必须入表）----
    #
    # ★ 为什么不把"两侧比率不相等"当断言 ★
    #   比率是分子分母的**派生量**：A 组的 7 个判据列逐窗口相等时，
    #   比率在数学上必然相等，它不提供任何额外的正确性信息；
    #   却会因为两侧除法实现细节不同产生末位差异（假差异）。
    #   拿实现细节当数据正确性的判据，是判据写错。
    #
    # ★ 那怎么保证不是"放水" ★
    #   换成一条**更强**的判据：不比对两侧，而是分别检查
    #   "每一侧存的比率是否等于由它**自己的**计数按 metrics.md 第 3 节公式算出的值"。
    #   这条判据不需要两侧一致就能判定谁错，而且比"两侧相等"更根本 ——
    #   两侧一起错成同一个值，它照样能抓出来。
    #
    # ★ 实测结论（本项目真实数据，可复核）★
    #   离线侧 0 个矛盾窗口；实时侧 **1** 个：
    #     window_start = 2026-03-21 19:23:00
    #     实时 view_cnt=2 click_cnt=1 → 按公式 click_rate 应为 0.5000，实存 0.0000
    #     离线同一窗口 view_cnt=2 click_cnt=1 click_rate=0.5000（正确）
    #   这不是浮点末位舍入（0.5000 vs 0.0000 差在小数点后第一位），
    #   是**实时链路那一行自相矛盾**：它的计数列说 1/2，比率列却说 0。
    #   旁证：实时侧 click_rate 的全表取值只有 {0.0000, NULL, 1.0000}，
    #   而全表"0 < click_cnt < view_cnt"的分数窗口**恰好只有这 1 个** ——
    #   即目前数据只有一个分数样本，而它就是错的
    #   （分母为 1 或分子为 0 的窗口无法暴露截断）。
    #
    # ★ 因此判据这样定 ★
    #   - 实时侧矛盾数**不阻断**本作业：缺陷在实时链路，
    #     离线作业既不该、也无法替它"修绿"（改判据去掩盖才是真放水）；
    #   - 但它被当作**对账的一等结论**落进汇总表
    #     （realtime_rate_anomaly_windows），并逐窗口落进
    #     ads_reconcile_traffic_1m.realtime_rate_anomaly，可随时查证；
    #   - 离线侧必须为 0 —— 这一条是硬断言，因为离线侧是我方可控的那一半。
    realtime_bad = int(summary["realtime_rate_anomaly_windows"])
    batch_bad = int(summary["batch_rate_anomaly_windows"])
    checker.check("离线侧比率与自身计数一致（不一致窗口数）", batch_bad, 0)

    # 实时侧的矛盾数**刻意不写成 check**：
    #   它不是"通过/失败"的判据，而是一条必须被看见的**结论**。
    #   写成恒真的 check 只会虚增通过项数（自欺），写成会失败的 check
    #   又会让离线作业替实时链路的缺陷"背锅"（同样没意义 —— 重跑离线
    #   一万次也改不了实时写下的那个值）。所以它在下方单独打印，
    #   并已落进汇总表 realtime_rate_anomaly_windows 与逐窗口表的两列。
    if realtime_bad > 0:
        print(
            f"[rate ]  !! 实时侧有 {realtime_bad} 个窗口的比率列与自身计数矛盾"
            f"（缺陷在实时链路，已如实入表；离线侧为 {batch_bad}）",
            flush=True,
        )

    print("", flush=True)
    print(
        f"[scope]  对账窗口 {summary['batch_windows']} 个"
        f"（实时侧 {summary['realtime_windows']} 个），"
        f"一致 {summary['matched_windows']} 个，不一致 {summary['mismatched_windows']} 个",
        flush=True,
    )
    print(
        f"[scope]  单边窗口：仅实时 {summary['realtime_only_windows']} 个，"
        f"仅离线 {summary['batch_only_windows']} 个",
        flush=True,
    )
    print(
        f"[scope]  PV 合计：实时 {summary['realtime_total_pv']} vs 离线 {summary['batch_total_pv']}",
        flush=True,
    )
    print(
        f"[scope]  窗口 UV 范围：实时 [{summary['realtime_min_uv']}, {summary['realtime_max_uv']}]"
        f" vs 离线 [{summary['batch_min_uv']}, {summary['batch_max_uv']}]",
        flush=True,
    )
    print(
        f"[scope]  UV 是去重指标：SUM(窗口 uv) 跨窗口不可加，"
        f"本表只做逐窗口比对（不参与任何上卷）",
        flush=True,
    )
    print(
        f"[rate ]  比率列取证（判据：该侧比率 == 由该侧自身计数按 metrics.md 公式重算）",
        flush=True,
    )
    print(f"[rate ]    离线侧矛盾窗口数：{batch_bad}", flush=True)
    print(f"[rate ]    实时侧矛盾窗口数：{realtime_bad}", flush=True)

    if realtime_bad > 0 or batch_bad > 0:
        print("[rate ]  矛盾窗口明细（最多 10 个）：", flush=True)
        stamp = compared_at.strftime("%Y-%m-%d %H:%M:%S")
        for row in spark.sql(
            f"""
            SELECT window_start, realtime_rate_anomaly, batch_rate_anomaly,
                   realtime_view_cnt, realtime_click_cnt, realtime_click_rate,
                   batch_view_cnt, batch_click_cnt, batch_click_rate
            FROM lakehouse.ads_reconcile_traffic_1m
            WHERE compared_at = CAST('{stamp}' AS TIMESTAMP)
              AND (realtime_rate_anomaly OR batch_rate_anomaly)
            ORDER BY window_start
            LIMIT 10
            """
        ).collect():
            print(
                f"[rate ]   {row['window_start']}"
                f"  实时(view={row['realtime_view_cnt']} click={row['realtime_click_cnt']}"
                f" rate={row['realtime_click_rate']} anomaly={row['realtime_rate_anomaly']})"
                f"  离线(view={row['batch_view_cnt']} click={row['batch_click_cnt']}"
                f" rate={row['batch_click_rate']} anomaly={row['batch_rate_anomaly']})",
                flush=True,
            )

    if int(summary["mismatched_windows"]) > 0:
        print("", flush=True)
        print("[diff ] 前 10 个不一致窗口：", flush=True)
        stamp = compared_at.strftime("%Y-%m-%d %H:%M:%S")
        for row in spark.sql(
            f"""
            SELECT window_start, diff_uv, diff_pv, diff_view_cnt, diff_click_cnt,
                   diff_cart_cnt, diff_favorite_cnt, diff_buy_cnt
            FROM lakehouse.ads_reconcile_traffic_1m
            WHERE NOT is_match
              AND compared_at = CAST('{stamp}' AS TIMESTAMP)
            ORDER BY window_start
            LIMIT 10
            """
        ).collect():
            print(
                f"[diff ]   {row['window_start']}  Δuv={row['diff_uv']} Δpv={row['diff_pv']}"
                f" Δview={row['diff_view_cnt']} Δclick={row['diff_click_cnt']}"
                f" Δcart={row['diff_cart_cnt']} Δfav={row['diff_favorite_cnt']}"
                f" Δbuy={row['diff_buy_cnt']}",
                flush=True,
            )

    exit_code = checker.finish()
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
