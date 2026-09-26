# 数据服务（services/api）

批流一体智能数据分析平台的**只读数据服务**：把 Doris 里的实时指标
以 HTTP 接口的形式提供给前端看板与（未来的）AI Agent。

- 技术栈：FastAPI `0.141.1` + uvicorn `0.54.0` + mysql-connector-python `9.7.0`
- 访问入口（对外）：`http://<服务器IP>/data/api/`（经 Nginx 反向代理）
- 接口文档：`http://<服务器IP>/data/api/docs`
- 部署方式：**宿主机 systemd**（`data-platform-api.service`），不进 Docker

---

## 1. 目录结构

```text
services/api/
├── app/
│   ├── config.py        配置（口令只从 .env 注入；缺失则拒绝启动）
│   ├── sqlguard.py      SQL 安全守卫（纯函数，可单测）
│   ├── doris.py         只读 Doris 访问（参数绑定 / 超时 / DECIMAL 保精度）
│   ├── repository.py    所有 SQL 常量与结果整形
│   ├── metrics_doc.py   指标口径字典（运行时解析 sql/metadata/metrics.md）
│   ├── envelope.py      统一响应信封与错误模型
│   └── main.py          FastAPI 路由与统一异常处理
└── requirements.txt
```

## 2. 本地运行

```bash
# 依赖（复用仓库根 venv）
.venv/bin/pip install -r services/api/requirements.txt

# 需要 .env 中有 API_DORIS_PASSWORD（见 .env.example）
DATA_PLATFORM_ENV=./.env .venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 --app-dir services/api

# 试试
curl -s http://127.0.0.1:8000/health
curl -s 'http://127.0.0.1:8000/overview' | head -c 400
```

## 3. 接口一览

| 路径 | 说明 |
| --- | --- |
| `GET /health` | 健康检查（含"是否使用只读账号"） |
| `GET /overview` | 总览 KPI + 窗口新鲜度 + 类目 Top5 |
| `GET /metrics/trade?limit=60` | 交易指标时间序列（升序） |
| `GET /metrics/traffic?limit=60` | 流量指标时间序列 |
| `GET /metrics/category?limit=10&window_limit=0` | 类目排行（0=全量） |
| `GET /funnel?window_limit=1440` | 行为漏斗 |
| `GET /orders?limit=20&offset=0&category=&start=&end=` | 订单明细分页 |
| `GET /orders/{order_id}` | 订单 + 支付 + 退款 |
| `GET /meta/metrics` | **指标口径字典**（解析自 metrics.md） |
| `GET /meta/tables` | 表结构与分层 |
| `GET /categories` | 类目下拉选项 |

统一响应信封：

```json
{
  "data": { },
  "source": {
    "tables": ["ecommerce.ads_realtime_trade_1m"],
    "metric_definitions": [ { "metric": "GMV", "field": "gmv", "formula": "SUM(amount) …", "null_policy": "无事件补 0" } ],
    "time_range": { "start": "…", "end": "…" },
    "note": "口径说明"
  },
  "generated_at": "2026-09-26T13:51:51+08:00"
}
```

错误信封：`{"error": {"code": "…", "message": "中文说明", "detail": "…"}}`

## 4. 安全设计（三道锁）

| 锁 | 位置 | 内容 |
| --- | --- | --- |
| ① 数据库权限 | Doris | 专用账号 `agent_ro`，只有 `SELECT_PRIV`（写操作被 Doris 拒绝，验收脚本会实测） |
| ② SQL 守卫 | `sqlguard.py` | 仅 SELECT；拒绝多语句/注释/DDL/DML；**表白名单**；强制 LIMIT 并收敛到上限 |
| ③ 接口约束 | `main.py` + Nginx | 只注册 GET；参数经 FastAPI 校验；查询超时 15s；Nginx `limit_except GET HEAD OPTIONS` |

补充约定：

- 口令只从 `.env` 读取（`chmod 640 root:dpapi`），代码中无默认口令；
- 脚本/测试中传口令用环境变量 `MYSQL_PWD`（`/proc/<pid>/cmdline` 全局可读，`environ` 只有 root 可读）；
- 金额一律以**字符串**返回，避免浮点误差（前端按整数分累加）。

## 5. 口径一致性

`/meta/metrics` 与所有接口的 `source.metric_definitions` 都来自
[`sql/metadata/metrics.md`](../../sql/metadata/metrics.md)（**唯一权威口径**），
服务端在运行时解析该文档（按 mtime 缓存），不存在第二份口径副本。

空值约定：

- 可加指标（GMV / 笔数 / 金额 / PV）无事件时补 `0`；
- 比率指标（客单价、支付成功率、退款率、转化率）分母为 0 时返回 `null`。

## 6. 部署与验收

```bash
bash scripts/install-web.sh      # 首次安装（nginx + venv + 只读账号 + systemd）
bash scripts/deploy-web.sh       # 更新代码后重启服务
bash scripts/verify-sprint-6.sh  # 7 步验收（含对账与安全验证）
journalctl -u data-platform-api -n 100 --no-pager   # 看日志
```

## 7. 测试

```bash
python -m pytest -m unit                    # SQL 守卫 + 口径解析（不依赖容器）
python -m pytest -m smoke tests/smoke/test_api.py   # 真实接口冒烟（需服务已启动）
```
