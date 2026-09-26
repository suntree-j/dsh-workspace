-- ============================================================
-- Sprint 1 — Flink SQL：ADS / DWS 指标作业（1 分钟滚动窗口）
-- ============================================================
--
-- 依据：docs/sprint/SPRINT_1.md 第 3.3 / 3.4 节
-- 口径：sql/metadata/metrics.md（唯一权威，勿在此处另立口径）
--
-- 实现思路（交易总览）：
--   订单 / 支付 / 退款是三条独立的事件流，若用 JOIN 合并会在
--   某条流无数据时丢结果。因此采用「先各自窗口聚合 → UNION ALL
--   成统一 (指标名, 值) 形态 → 外层按窗口做条件聚合透视」的方式，
--   把窄表转成宽表写入 ads_realtime_trade_1m。
--
-- 空值约定（两类指标，处理方式不同）：
--   1) 可加指标（GMV / 金额 / 笔数）：1 分钟窗口是固定骨架，
--      「该窗口没有这类事件」应表达为 0，而不是 NULL ——
--      实测发现：支付发生在下单之后的另一分钟，若不做处理，
--      某窗口会出现 gmv=23616 而 payment_cnt=NULL，下游容易误读为"丢数据"。
--   2) 比率类指标：分母为 0 时仍置 NULL，见 metrics.md 第 1 节。
-- ============================================================

-- ------------------------------------------------------------
-- 作业 1：ADS 实时交易总览
-- ------------------------------------------------------------
INSERT INTO sink_ads_realtime_trade_1m
SELECT
    window_start,
    window_start + INTERVAL '1' MINUTE AS window_end,
    COALESCE(SUM(CASE WHEN metric_name = 'gmv' THEN metric_value END),
             CAST(0 AS DECIMAL(18, 2))) AS gmv,
    CAST(COALESCE(SUM(CASE WHEN metric_name = 'order_cnt' THEN metric_value END),
                  CAST(0 AS DECIMAL(18, 2))) AS BIGINT) AS order_cnt,
    CAST(COALESCE(SUM(CASE WHEN metric_name = 'order_user_cnt' THEN metric_value END),
                  CAST(0 AS DECIMAL(18, 2))) AS BIGINT) AS order_user_cnt,
    CASE
        WHEN SUM(CASE WHEN metric_name = 'order_cnt' THEN metric_value END) > 0
        THEN CAST(SUM(CASE WHEN metric_name = 'gmv' THEN metric_value END)
                  / SUM(CASE WHEN metric_name = 'order_cnt' THEN metric_value END)
                  AS DECIMAL(18, 2))
    END AS avg_order_amount,
    CAST(COALESCE(SUM(CASE WHEN metric_name = 'payment_cnt' THEN metric_value END),
                  CAST(0 AS DECIMAL(18, 2))) AS BIGINT) AS payment_cnt,
    COALESCE(SUM(CASE WHEN metric_name = 'payment_amount' THEN metric_value END),
             CAST(0 AS DECIMAL(18, 2))) AS payment_amount,
    CAST(COALESCE(SUM(CASE WHEN metric_name = 'payment_fail_cnt' THEN metric_value END),
                  CAST(0 AS DECIMAL(18, 2))) AS BIGINT) AS payment_fail_cnt,
    CASE
        WHEN SUM(CASE WHEN metric_name IN ('payment_cnt', 'payment_fail_cnt') THEN metric_value END) > 0
        THEN CAST(SUM(CASE WHEN metric_name = 'payment_cnt' THEN metric_value END)
                  / SUM(CASE WHEN metric_name IN ('payment_cnt', 'payment_fail_cnt') THEN metric_value END)
                  AS DECIMAL(10, 4))
    END AS payment_success_rate,
    CAST(COALESCE(SUM(CASE WHEN metric_name = 'refund_cnt' THEN metric_value END),
                  CAST(0 AS DECIMAL(18, 2))) AS BIGINT) AS refund_cnt,
    COALESCE(SUM(CASE WHEN metric_name = 'refund_amount' THEN metric_value END),
             CAST(0 AS DECIMAL(18, 2))) AS refund_amount,
    CASE
        WHEN SUM(CASE WHEN metric_name = 'payment_amount' THEN metric_value END) > 0
        THEN CAST(SUM(CASE WHEN metric_name = 'refund_amount' THEN metric_value END)
                  / SUM(CASE WHEN metric_name = 'payment_amount' THEN metric_value END)
                  AS DECIMAL(10, 4))
    END AS refund_rate
FROM (
    -- 订单流
    SELECT
        window_start,
        'gmv' AS metric_name,
        SUM(amount) AS metric_value
    FROM TABLE(TUMBLE(TABLE src_order_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
    WHERE event_type = 'ORDER_CREATED'
    GROUP BY window_start

    UNION ALL

    SELECT
        window_start,
        'order_cnt' AS metric_name,
        CAST(COUNT(*) AS DECIMAL(18, 2)) AS metric_value
    FROM TABLE(TUMBLE(TABLE src_order_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
    WHERE event_type = 'ORDER_CREATED'
    GROUP BY window_start

    UNION ALL

    SELECT
        window_start,
        'order_user_cnt' AS metric_name,
        CAST(COUNT(DISTINCT user_id) AS DECIMAL(18, 2)) AS metric_value
    FROM TABLE(TUMBLE(TABLE src_order_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
    WHERE event_type = 'ORDER_CREATED'
    GROUP BY window_start

    UNION ALL

    -- 支付流：成功笔数
    SELECT
        window_start,
        'payment_cnt' AS metric_name,
        CAST(COUNT(*) AS DECIMAL(18, 2)) AS metric_value
    FROM TABLE(TUMBLE(TABLE src_payment_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
    WHERE event_type = 'PAYMENT_SUCCESS'
    GROUP BY window_start

    UNION ALL

    SELECT
        window_start,
        'payment_fail_cnt' AS metric_name,
        CAST(COUNT(*) AS DECIMAL(18, 2)) AS metric_value
    FROM TABLE(TUMBLE(TABLE src_payment_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
    WHERE event_type = 'PAYMENT_FAILED'
    GROUP BY window_start

    UNION ALL

    SELECT
        window_start,
        'payment_amount' AS metric_name,
        SUM(amount) AS metric_value
    FROM TABLE(TUMBLE(TABLE src_payment_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
    WHERE event_type = 'PAYMENT_SUCCESS'
    GROUP BY window_start

    UNION ALL

    -- 退款流
    SELECT
        window_start,
        'refund_cnt' AS metric_name,
        CAST(COUNT(*) AS DECIMAL(18, 2)) AS metric_value
    FROM TABLE(TUMBLE(TABLE src_refund_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
    WHERE event_type = 'REFUND_CREATED'
    GROUP BY window_start

    UNION ALL

    SELECT
        window_start,
        'refund_amount' AS metric_name,
        SUM(refund_amount) AS metric_value
    FROM TABLE(TUMBLE(TABLE src_refund_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
    WHERE event_type = 'REFUND_CREATED'
    GROUP BY window_start
) t
GROUP BY window_start;

-- ------------------------------------------------------------
-- 作业 2：DWS 流量总览（ADS 流量表的数据基础）
-- ------------------------------------------------------------
INSERT INTO sink_dws_traffic_overview_1m
SELECT
    window_start,
    window_start + INTERVAL '1' MINUTE AS window_end,
    COUNT(*) AS pv,
    COUNT(DISTINCT user_id) AS uv,
    SUM(CASE WHEN event_type = 'VIEW'     THEN 1 ELSE 0 END) AS view_cnt,
    SUM(CASE WHEN event_type = 'CLICK'    THEN 1 ELSE 0 END) AS click_cnt,
    SUM(CASE WHEN event_type = 'CART'     THEN 1 ELSE 0 END) AS cart_cnt,
    SUM(CASE WHEN event_type = 'FAVORITE' THEN 1 ELSE 0 END) AS favorite_cnt,
    SUM(CASE WHEN event_type = 'BUY'      THEN 1 ELSE 0 END) AS buy_cnt
FROM TABLE(TUMBLE(TABLE src_behavior_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
GROUP BY window_start;

-- ------------------------------------------------------------
-- 作业 3：ADS 实时流量（含转化率，分母为 0 置 NULL）
-- ------------------------------------------------------------
INSERT INTO sink_ads_realtime_traffic_1m
SELECT
    window_start,
    window_start + INTERVAL '1' MINUTE AS window_end,
    COUNT(DISTINCT user_id) AS uv,
    COUNT(*) AS pv,
    SUM(CASE WHEN event_type = 'VIEW'     THEN 1 ELSE 0 END) AS view_cnt,
    SUM(CASE WHEN event_type = 'CLICK'    THEN 1 ELSE 0 END) AS click_cnt,
    SUM(CASE WHEN event_type = 'CART'     THEN 1 ELSE 0 END) AS cart_cnt,
    SUM(CASE WHEN event_type = 'FAVORITE' THEN 1 ELSE 0 END) AS favorite_cnt,
    SUM(CASE WHEN event_type = 'BUY'      THEN 1 ELSE 0 END) AS buy_cnt,
    CASE WHEN SUM(CASE WHEN event_type = 'VIEW' THEN 1 ELSE 0 END) > 0
         THEN CAST(SUM(CASE WHEN event_type = 'CLICK' THEN 1 ELSE 0 END)
                   / SUM(CASE WHEN event_type = 'VIEW' THEN 1 ELSE 0 END)
                   AS DECIMAL(10, 4)) END AS click_rate,
    CASE WHEN SUM(CASE WHEN event_type = 'CLICK' THEN 1 ELSE 0 END) > 0
         THEN CAST(SUM(CASE WHEN event_type = 'CART' THEN 1 ELSE 0 END)
                   / SUM(CASE WHEN event_type = 'CLICK' THEN 1 ELSE 0 END)
                   AS DECIMAL(10, 4)) END AS cart_rate,
    CASE WHEN SUM(CASE WHEN event_type = 'CART' THEN 1 ELSE 0 END) > 0
         THEN CAST(SUM(CASE WHEN event_type = 'BUY' THEN 1 ELSE 0 END)
                   / SUM(CASE WHEN event_type = 'CART' THEN 1 ELSE 0 END)
                   AS DECIMAL(10, 4)) END AS buy_rate
FROM TABLE(TUMBLE(TABLE src_behavior_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
GROUP BY window_start;

-- ------------------------------------------------------------
-- 作业 4：ADS 实时类目销售（窗口 × 类目）
-- 类目来自事件中的冗余维度字段，无需 join 维表
--
-- !! 列顺序必须与 sink / Doris 表一致（实测踩坑） !!
--   Doris 要求 UNIQUE KEY(window_start, category_name) 是 schema 的
--   有序前缀，因此 sink 表也按 (window_start, category_name, ...)
--   声明。Flink 写入是**按位置**对齐的，顺序不一致会在编译期报：
--     ValidationException: Column types of query result and sink for
--     '...sink_ads_realtime_category_1m' do not match.
--   故此处 window_end 放在 category_name 之后。
-- ------------------------------------------------------------
INSERT INTO sink_ads_realtime_category_1m
SELECT
    window_start,
    COALESCE(category_name, 'UNKNOWN') AS category_name,
    window_start + INTERVAL '1' MINUTE AS window_end,
    COUNT(*) AS order_cnt,
    SUM(amount) AS gmv,
    CAST(SUM(quantity) AS BIGINT) AS total_quantity,
    CASE WHEN COUNT(*) > 0
         THEN CAST(SUM(amount) / COUNT(*) AS DECIMAL(18, 2)) END AS avg_order_amount
FROM TABLE(TUMBLE(TABLE src_order_event, DESCRIPTOR(event_time), INTERVAL '1' MINUTE))
WHERE event_type = 'ORDER_CREATED'
GROUP BY window_start, COALESCE(category_name, 'UNKNOWN');
