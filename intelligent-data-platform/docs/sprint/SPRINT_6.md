# Sprint 6 设计：数据后台 + 前后端（服务层）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> Sprint：6（**顺序前移，见第 1.1 节**）
> 状态：✅ 已实现并在腾讯云服务器通过验收（2026-09-26）
> 前置：Sprint 0（基础环境）+ Sprint 1（Kafka → Flink → Doris 实时数仓）已完成

---

## 1. Sprint 6 目标

```text
浏览器 ──► Nginx(:80)
              ├── /data/             → Vue 静态看板（services/web）
              └── /data/api/         → FastAPI 只读数据服务（systemd）
                                          │  只读账号 + SQL 守卫
                                          ▼
                                     Doris DWD / DWS / ADS
```

**只做这一层**：把已经跑通的实时数仓**变成可访问的数据产品** ——
人能在浏览器里看到指标，程序能通过只读 API 拿到指标，
且每一次查询都能说明"数据从哪张表、按什么口径算出来的"。

### 1.1 为什么 Sprint 6 提前做（与原始 Roadmap 的偏差）

原始 Roadmap（见 `SPRINT_0.md` 第 25 节）的顺序是
`S2 Spark+Hive+HDFS → S3 分层建模 → S4 Airflow → S5 Iceberg → S6 Backend+Dashboard`。

本次按项目负责人要求**把 Sprint 6 前移**，理由是：

| 理由 | 说明 |
| --- | --- |
| 验收可视化 | Sprint 1 的成果目前只能在命令行里查 Doris；有看板才能直观验收实时链路 |
| 接口先行 | 后续 Sprint 7~10（LLM / Agent / RAG / MCP）全部依赖"可查询的只读数据接口"，先把它做出来可以让智能层与离线层并行推进 |
| 口径固化 | 看板与 API 直接消费 `sql/metadata/metrics.md`，等于把口径字典先落到产品里，避免以后各写一套 |
| 风险可控 | 服务层不触碰数据层（只读账号 + 只查 ADS/DWD 表），不会影响已验收的实时链路 |

**代价（如实记录）**：离线链路（Spark/Iceberg/Airflow）仍未开始，
因此当前看板展示的是**实时链路指标**，还没有"离线对账"这一层；
Sprint 2/3 完成后再把离线结果接进同一个 API（`/metrics/*` 增加 `source=offline` 参数），
届时两条链路的数据可以并排对比 —— 这正是"批流一体"要展示的东西。

### 1.2 明确不做

```text
❌ 用户登录 / 权限体系（本轮只做只读公开看板，鉴权留给后续）
❌ 写接口（本项目所有数据写入都在数据层：Flink / Routine Load）
❌ 引入 Spring Boot（避免为一个只读接口再引入 JVM 技术栈）
❌ 引入 npm / Vite / webpack 构建链（服务器装 Node 只为打包静态页，性价比低）
❌ 引入 Redis 缓存、Prometheus 监控（Sprint 11 再说）
```

---

## 2. 技术选型

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| FastAPI | `0.141.1` | 只读数据服务（自带 OpenAPI 文档） |
| uvicorn | `0.54.0` | ASGI 服务器（纯 Python 版，不装 `[standard]` 以免编译依赖） |
| mysql-connector-python | `9.7.0` | 访问 Doris（兼容 MySQL 协议），复用 Sprint 0 已装依赖 |
| Nginx | `1.24.0`（apt） | 静态托管 + 反向代理 + gzip |
| Vue | `3.5.13`（全局构建） | 前端框架，**无构建步骤** |
| ECharts | `5.6.0` | 图表库，本地 vendor，不依赖外部 CDN |
| systemd | 系统自带 | 进程守护（`data-platform-api.service`） |

### 2.1 关键决策：服务层直接装在宿主机，不进 Docker

| | 数据层（Sprint 0/1） | 服务层（Sprint 6） |
| --- | --- | --- |
| 部署方式 | **Docker Compose**（保留） | **apt + systemd + Nginx**（新增） |
| 理由 | Doris / Kafka / Flink 手工安装风险高、依赖多、需要版本绑定；且已用命名卷稳定运行 | 只有两个进程（uvicorn + nginx），用 systemd 管理更直接：`journalctl -u` 看日志、`nginx -t` 校验配置、`systemctl restart` 重启，少一层容器网络与端口映射 |

**所以"不用 Docker"指的是新增的服务层**：
把已经稳定运行的 Doris/Kafka/Flink 从容器里搬出来重新手工安装，
属于**高风险、零收益**的操作，本项目不做。

---

## 3. 数据服务设计（services/api）

### 3.1 分层

```text
services/api/app/
├── config.py        配置（口令只从 .env 注入，缺失则拒绝启动）
├── sqlguard.py      SQL 安全守卫（纯函数，可单测）
├── doris.py         只读 Doris 访问（参数绑定 + 超时 + DECIMAL 保精度）
├── repository.py    所有 SQL 常量与结果整形
├── metrics_doc.py   指标口径字典（解析 sql/metadata/metrics.md）
├── envelope.py      统一响应信封 {data, source, generated_at}
└── main.py          FastAPI 路由与统一异常处理
```

### 3.2 接口清单（全部 GET，前缀 `/data/api`）

| 路径 | 作用 |
| --- | --- |
| `/health` | 服务与 Doris 连通性 + **是否使用只读账号** |
| `/overview` | 总览 KPI（全量口径）+ 窗口新鲜度 + 类目 Top5 |
| `/metrics/trade` | 交易指标时间序列（最近 N 个 1 分钟窗口，升序） |
| `/metrics/traffic` | 流量指标时间序列 |
| `/metrics/category` | 类目销售排行（可切全量/最近 N 分钟） |
| `/funnel` | 行为漏斗（浏览→点击→加购→购买） |
| `/orders` | 订单明细分页（类目、时间范围筛选） |
| `/orders/{order_id}` | 单笔订单 + 支付 + 退款（跨三张 DWD 表） |
| `/meta/metrics` | **指标口径字典**（直接解析 `metrics.md`） |
| `/meta/tables` | 表结构与分层（来自 `information_schema`） |
| `/categories` | 类目下拉选项 |

### 3.3 统一响应信封（可追溯来源）

```json
{
  "data":    { "...": "业务数据" },
  "source": {
    "tables": ["ecommerce.ads_realtime_trade_1m"],
    "metric_definitions": [{ "metric": "GMV", "field": "gmv", "formula": "...", "null_policy": "无事件补 0" }],
    "time_range": { "start": "2024-10-25 08:16:00", "end": "2026-09-26 11:37:00" },
    "note": "全量口径：对所有 1 分钟窗口求和"
  },
  "generated_at": "2026-09-26T13:51:51+08:00"
}
```

**为什么每个响应都要带 `source`**：这是 `AGENTS.md` 第 10.1 节
「Agent 的最终回答必须能够说明数据来源」在接口层的落地 ——
前端把它显示在每页底部，未来的 Agent 直接复用它组织回答，
不需要另建一套血缘数据。

### 3.4 安全设计（三道锁）

| 锁 | 位置 | 内容 |
| --- | --- | --- |
| 1. 数据库权限 | Doris | 专用账号 `agent_ro`，只有 `SELECT_PRIV`（实测建表被拒：`Access denied; you need (CREATE)`） |
| 2. SQL 守卫 | `sqlguard.py` | 只允许 SELECT；拒绝多语句/注释/DDL/DML；**表白名单**；强制 LIMIT 并收敛到上限 |
| 3. 接口约束 | `main.py` | 只注册 GET 路由；参数用 FastAPI 校验（`limit ≤ 200`、日期正则）；查询超时 15s、连接超时 5s；Nginx `limit_except GET HEAD OPTIONS` |

口令处理：只从 `.env` 读取（`chmod 640 root:dpapi`），
代码中没有任何默认口令；测试与脚本中通过 `MYSQL_PWD` 环境变量传递
（`/proc/<pid>/cmdline` 全局可读，而 `environ` 只有 root 可读）。

### 3.5 指标口径字典来自文档本身

`/meta/metrics` 不存第二份口径，而是**运行时解析 `sql/metadata/metrics.md`**
（按 mtime 缓存）。文档里表格的"同上"会被还原成真实表名，
域（交易/流量/类目）与空值约定一起解析出来。

这样做的意义：口径只有一份，改了文档刷新页面就生效，
不存在"文档写的和接口返回的不一致"。

---

## 4. 前端设计（services/web）

### 4.1 无构建步骤

```text
services/web/
├── index.html     外壳 + 6 个页面模板（x-template）+ 内联 SVG 图标
├── app.js         Vue 3 组合式 API：API 客户端 / 图表生命周期 / 路由 / 6 个面板
├── styles.css     深色数据大屏样式
└── vendor/        vue.global.prod.js（155 KB）、echarts.min.js（1.0 MB）
                   ← 由 scripts/install-web.sh 从 npmmirror 下载，不入 Git
```

不用 npm/Vite 的理由：本项目只有 6 个页面、无第三方业务依赖，
引入构建链会带来"服务器要装 Node、产物要单独发布、版本要锁"的额外成本；
用 Vue 的**全局构建 + 浏览器内模板**已经足够，而且源码即产物，
排查问题时看到的就是运行时的代码。

### 4.2 六个页面

| 页面 | 内容 |
| --- | --- |
| 总览 | KPI 卡片（GMV/订单量/支付/退款/UV/PV，带口径 tooltip）+ 交易趋势 + 流量趋势 + 类目 Top5 + 明细快照 |
| 交易分析 | GMV/客单价/支付成功率/退款率 + 双轴趋势 + 窗口明细表 |
| 流量分析 | UV/PV + 漏斗 + 转化率 + 趋势 |
| 类目销售 | GMV 排行 + 占比 + 明细（可切"最近 N 分钟 / 全量"） |
| 订单明细 | 类目/时间筛选 + 分页 + 点击查看订单详情（含支付与退款） |
| 指标口径 | 指标字典（域/指标/字段/表/口径/计算方式/空值约定）+ 表结构分层 |

### 4.3 与口径一致的两个前端约定

1. **金额按字符串处理**：后端返回 `"51890375.77"`，前端展示补零加千分位，
   跨行累加先转整数分（`BigInt`）再算，绝不用浮点累加。
2. **`null` 显示为 `—`**：比率类指标分母为 0 时后端返回 `null`，
   前端显示 `—`（不是 0）；可加指标后端已补 0，正常显示 `0.00`。

---

## 5. 部署设计

```text
scripts/install-web.sh    首次安装：apt 装 nginx → venv 装依赖 → 建 dpapi 用户
                          → 建 Doris 只读账号 → 下载前端运行时 → 装 nginx/systemd → 自检
scripts/deploy-web.sh     日常更新：检查前端文件与运行时 → 同步依赖 → 重载配置 → 重启服务 → 自检
scripts/verify-sprint-6.sh 验收：服务状态 / 前端文件 / Nginx 路径 / API 健康 /
                          指标对账 / 安全验证 / 自动化测试（7 步）
deploy/nginx/data-platform.conf        站点配置（/data/ 静态 + /data/api/ 反代）
deploy/systemd/data-platform-api.service 进程守护（专用用户 + 基础加固）
```

访问地址（验收环境）：`http://36.151.150.140/data/`
接口文档：`http://36.151.150.140/data/api/docs`

---

## 6. 完成标准（Definition of Done）

- [x] `bash scripts/install-web.sh` 一键安装成功，可重复执行
- [x] Nginx 与 `data-platform-api` 服务均 active，开机自启
- [x] `/data/` 返回前端页面（200），`/data/api/health` 返回 200
- [x] API 使用**专用只读账号**（`readonly_enforced = true`），写操作被 Doris 拒绝
- [x] SQL 守卫有单元测试覆盖（危险语句逐条拒绝）
- [x] 指标与 MySQL **精确对账一致**（API GMV == MySQL GMV，精确到分）
- [x] 前端 6 个页面在真实浏览器中渲染正常（图表有 canvas、KPI 有值）
- [x] `python -m pytest -m unit` 55 passed；`-m smoke tests/smoke/test_api.py` 17 passed
- [x] `bash scripts/verify-sprint-6.sh` 7/7 PASS
- [x] 文档同步：本文件 + `DEVELOPMENT_LOG.md` + `README.md` + `AGENTS.md`
- [x] 未引入 Sprint 7+ 的任何组件（无 LLM / 无 Agent / 无 RAG / 无 MCP / 无监控）

---

## 7. 已知限制（诚实记录）

| 限制 | 影响 | 计划 |
| --- | --- | --- |
| **接口无鉴权** | 知道 IP 的人都能读指标（只读，不能改数据） | 后续 Sprint 加 Token/登录；目前是毕业设计演示环境 |
| 单 worker（`--workers 1`） | 并发高时排队 | 服务器 4 核，看板场景足够；需要时加 worker 或上连接池 |
| 无缓存 | 每次请求都查 Doris | 查询本身 <50ms；后续可加 5 秒 TTL 缓存 |
| 前端运行时需联网下载一次 | 内网离线环境首次安装会失败 | 可预先把两个 vendor 文件拷进 `services/web/vendor/` |
| 看板只覆盖实时链路 | 还没有离线（Spark/Iceberg）结果可对比 | Sprint 2/3 完成后接同一 API |
| Doris 前端（FE）仍是单点 | 查询层无高可用 | 开发环境定位，论文中说明生产差异 |
