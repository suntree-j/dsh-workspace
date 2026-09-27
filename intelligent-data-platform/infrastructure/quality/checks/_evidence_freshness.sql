-- 新鲜度证据（伴随查询）：一次给出"现在 / 最新数据 / 滞后小时"三项
-- 供人肉核对 —— 校验只报滞后量时，看不到它到底比的是什么时间
SELECT NOW() AS now_ts, (SELECT MAX(window_start) FROM lakehouse_ads.ads_traffic_1m) AS latest_window, ROUND(TIMESTAMPDIFF(MINUTE, (SELECT MAX(window_start) FROM lakehouse_ads.ads_traffic_1m), NOW()) / 60, 2) AS lag_hours;
