# 数据服务（services/api）

批流一体智能数据分析平台的**只读数据服务**：把 Doris 里的实时指标
以 HTTP 接口的形式提供给前端看板与（未来的）AI Agent。

- 技术栈：FastAPI `0.141.1` + uvicorn `0.54.0` + mysql-connector-python `9.7.0`
- 访问入口（对外）：`https://<服务器IP>/data/api/`（经 Nginx 反向代理，TLS 终止在 Nginx）
- 接口文档：`https://<服务器IP>/data/api/docs`
- 部署方式：**宿主机 systemd**（`data-platform-api.service`），不进 Docker

> ⚠️ 站点用**自签证书**（按 IP 访问，受信任的 CA 不为裸 IP 签证书），
> 浏览器首次访问需点一次「高级」→「继续前往」。
> 为什么要上 HTTPS：明文 HTTP 在公网链路上会被中间设备改写，
> 约 40% 的请求变成"无 `Server` 头、空响应体"的 502。
> 详见 [`docs/sprint/SPRINT_6.md`](../../docs/sprint/SPRINT_6.md) 第 8 节。

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

### 3.1 实时链路（Flink → Doris，分钟粒度）

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
| `GET /meta/tables` | 表结构与分层（覆盖实时库与离线库） |
| `GET /categories` | 类目下拉选项 |

### 3.2 离线链路（Spark → 湖仓 → Doris，天粒度）

> 为什么单独一组路径而不是给上面加 `source` 参数：
> 两条链路的**时间语义不同** —— 实时是 1 分钟窗口（秒级新鲜），
> 离线是按天（准确、可回溯）。混在一个响应里，前端必须写一堆 if
> 去解释"这个数是哪来的"；分开之后，**路径本身就是数据来源**。

| 路径 | 说明 |
| --- | --- |
| `GET /batch/overview?days=60` | 离线链路总览：全量 KPI + 按天序列 + 类目 Top10 |
| `GET /batch/reconcile` | **批流对账结论**：最近一批的区间/窗口数/不一致数、实时 vs 离线全量对比与差异、差异明细（最多 20 个窗口） |

`/batch/reconcile` 的 `data.deltas` 是"离线 − 实时"的字符串差额。
任一侧缺数据时返回 `null` 而**不是 0** —— 把"没有数据"当成 0
会让差异看起来是 0，从而掩盖真实的不一致。

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

**离线链路的指标口径与实时链路逐字相同**：

```text
实时  repository.trade_kpi()          ↔  离线  repository.batch_trade_kpi()
实时  repository.trade_series()       ↔  离线  repository.batch_trade_daily()
实时  repository.category_top()       ↔  离线  repository.batch_category_top()
```

两条链路谁算错了，由离线 Spark 作业产出的
`lakehouse_ads.ads_reconcile_trade_1m` 逐窗口记录下来，
`/batch/reconcile` 只是把这份结论读出来展示 —— 服务层不参与对账，
也不做任何"看起来成功"的兜底（AGENTS.md 第 10.3 节）。

空值约定：

- 可加指标（GMV / 笔数 / 金额 / PV）无事件时补 `0`；
- 比率指标（客单价、支付成功率、退款率、转化率）分母为 0 时返回 `null`。

## 6. 部署与验收

```bash
bash scripts/install-web.sh      # 首次安装（nginx + venv + 只读账号 + systemd）
bash scripts/deploy-web.sh       # 更新代码后重启服务
bash scripts/verify-sprint-6.sh  # 服务层 7 步验收（含对账与安全验证）
bash scripts/verify-sprint-3.sh  # 离线分层 + 批流对账 8 步验收（含服务装载校验）
journalctl -u data-platform-api -n 100 --no-pager   # 看日志
```

装载离线指标（把湖仓结果搬进 Doris 的 `lakehouse_ads`）：

```bash
bash scripts/load-batch-to-doris.sh   # S3() TVF 直读 Parquet + 行数对账 + 只读账号验证
```

## 7. 测试

```bash
python -m pytest -m unit                    # SQL 守卫 + 口径解析（不依赖容器）
python -m pytest -m smoke tests/smoke/test_api.py   # 真实接口冒烟（需服务已启动）
```

> 服务器上用项目虚拟环境：`/opt/data-platform/.venv/bin/python -m pytest`
> （系统 python3 里没有 pytest，验收脚本会自动优先选用 `.venv`）。
