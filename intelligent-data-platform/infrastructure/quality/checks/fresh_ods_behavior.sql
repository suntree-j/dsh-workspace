-- 推进滞后：DWD 行为明细最新 dt 落后实时链路的小时数
--
-- 这条判据守的是"贴源归档 → DWD"这一段：实时链路的 1m 窗口已经到哪天，
-- DWD 明细就应该到哪天。两者都是真实事件时间，与系统时钟无关。
-- 不与 NOW() 比的原因见 fresh_ads_trade_1m.sql。
SELECT ROUND(TIMESTAMPDIFF(MINUTE, (SELECT MAX(dt) FROM ecommerce.dwd_traffic_behavior_detail), (SELECT CAST(MAX(window_start) AS DATE) FROM ecommerce.ads_realtime_traffic_1m)) / 60, 2) AS lag_hours;
