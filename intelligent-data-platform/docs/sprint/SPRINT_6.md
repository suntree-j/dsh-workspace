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
                          → 建 Doris 只读账号 → 下载前端运行时 → 生成自签证书
                          → 装 nginx/systemd → 自检
scripts/deploy-web.sh     日常更新：检查前端文件与运行时 → 同步依赖 → 重载配置 → 重启服务 → 自检
scripts/setup-tls.sh      生成自签证书（幂等；Sprint 7 期间引入，见第 8 节）
scripts/verify-sprint-6.sh 验收：服务状态 / 前端文件 / Nginx 路径 / API 健康 /
                          指标对账 / 安全验证 / 自动化测试（7 步）
deploy/nginx/data-platform.conf        站点配置（:443 业务 + :80 探针与跳转）
deploy/systemd/data-platform-api.service 进程守护（专用用户 + 基础加固）
```

访问地址（验收环境）：`https://36.151.150.140/data/`
接口文档：`https://36.151.150.140/data/api/docs`

> ⚠️ 首次访问浏览器会提示"证书不受信任"——站点按 IP 访问，
> 受信任的 CA 不为裸 IP 签发证书，因此用的是**自签证书**。
> 点「高级」→「继续前往」即可。原因见第 8 节。

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
      （Sprint 7 加入 `/query` 后为 74 / 21 —— 计数增长来自新增用例，Sprint 6 的用例未被删改）
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

---

## 8. 后续变更：站点启用 HTTPS（Sprint 7 期间，2026-09-26）

> 本节记录一次**在 Sprint 7 期间发现并解决的 Sprint 6 遗留问题**。
> 放在这里而不是 SPRINT_7.md，是因为它改的是服务层入口本身，
> 与 Agent 无关；SPRINT_7.md 第 9.7 节只做交叉引用。

### 8.1 现象

客户端经公网访问 `http://36.151.150.140/data/*`，约 **15~40%** 的请求返回 **502**。

### 8.2 排查过程（每一步都在排除一种可能）

| # | 观察 | 排除掉的假设 |
| --- | --- | --- |
| 1 | 该 502 **没有 `Server` 响应头、响应体为空** | **不是 nginx 发的** —— nginx 的 502 必然带 `Server: nginx/1.24.0` 与一段 HTML 错误页 |
| 2 | TCP 80 十次连接全部成功（84 ms，`Test-NetConnection`） | 不是安全组 / 防火墙在丢 SYN |
| 3 | 服务端本机 `curl` 十次全部 200 | 不是应用或数据库的问题 |
| 4 | nginx 访问日志、error 日志里**没有任何 502** | 请求根本没进到 nginx |
| 5 | `netstat -s`：`ListenOverflows = 0` | 不是 accept 队列溢出（backlog 打满会表现为"连得上但被丢"） |
| 6 | 客户端本机跑着 VPN 代理（`iKuuuVPNCore`，监听 7890/7891） | 中间设备成了唯一剩下的解释 |

**结论**：明文 HTTP 在传输途中被中间设备改写。这类设备通常**不碰加密流量**。

> 排查方法比结论更值得记：**"这个 502 缺了什么"比"502 是什么"更有信息量**。
> 一个响应头的有无，直接把嫌疑从"我们的服务"缩小到"链路中间"。

### 8.3 处置

`deploy/nginx/data-platform.conf` 改为双 server 块：

```text
:443  ← 业务入口。TLS 终止在此，后面 /data/ 静态、/data/api/ 只读服务、
        /data/agent/ 问答 Agent 三个 location 与原来完全一致
:80   ← 只保留 /data/healthz 探针（监控通常只探 80，强制跳转会让探针拿到 301），
        其余一律 302 跳转到 HTTPS
```

证书由 `scripts/setup-tls.sh` 生成：自签、RSA 2048、有效期 3650 天，
`subjectAltName = DNS:localhost, IP:127.0.0.1, IP:<服务器IP>`。

> **为什么 SAN 必须包含 IP**：不含 IP 时浏览器报的是
> "证书对此地址无效"（看起来像配错了），含 IP 时报的才是
> "自签名证书"（看起来是预期的），两种提示对使用者的含义完全不同。

### 8.4 结果

```text
✅ 公网 HTTPS 可靠性      8 个路径 × 8 次 = 64/64 全部 200（改动前约 40% 为 502）
✅ HTTP 跳转              http://36.151.150.140/data/ → 302（10/10）
✅ 回归                    verify-sprint-6.sh 7/7 PASS；verify-sprint-7.sh 49/49 通过
✅ 真实浏览器（公网）       看板与问答页均完整渲染，图表 canvas 正常、取数正常
✅ 端到端 Agent            公网 HTTPS 提问 6.8s 返回，附 tables / executed_sql / steps
```

### 8.5 踩坑记录

| # | 坑 | 现象 | 处置 |
| --- | --- | --- | --- |
| 1 | **`http2 on;` 是 nginx 1.25.1+ 的语法** | 本项目钉 apt 的 **1.24.0**，写 `http2 on;` 会让 `nginx -t` 报 `unknown directive`，站点起不来 | 用 1.24.0 的写法：`listen 443 ssl http2;`，并在配置里写明**为什么不能照抄新写法** |
| 2 | 80 改为 302 后，**验收脚本集体误报失败** | 脚本里硬编码 `http://127.0.0.1/data/...` 且不跟随跳转，期望 200 实得 302 | 新增 `lib/common.sh` 的 `site_base()` / `site_scheme()` / `curl_site()`：有证书走 https、无证书回落 http。**证书是机器状态，不该让它在脚本里变成硬依赖** |
| 3 | 缺证书时 `nginx -t` 必失败，而配置**已经写进 sites-available** | 机器会停在"文件是坏的、进程还在跑旧的"状态，下次 nginx 重启直接起不来 | `deploy-web.sh` 改为：先确认/生成证书 → 备份站点文件 → 校验 → 失败自动还原；`install-web.sh` 首次安装时自动生成证书 |
| 4 | 根路径 `/` 返回 302 到 `/data/`，但 `/data`（无斜杠）**不在规则内** | 页面里 `./app.js` 这类相对路径会解析到根目录而 404 | 显式加 `location = /data { return 301 /data/; }` |
| 5 | 前后端脚本里打印的 URL 仍是 `http://` | 文档与终端输出会误导使用者（他复制过去会走明文那条有问题的路） | 统一改用 `site_scheme()` 拼装，不再手写协议 |

### 8.6 后续：TLS 已实现但当前**停用**（2026-09-26 决定）

第 8.1~8.5 节记录的 HTTPS 方案**没有作废，但当前不在生效状态**。

**决定**：暂时直接用 IP + 反向代理，不配证书；
后续注册域名、申请证书、完成备案之后再启用。

**原因**：按 IP 访问只能自签，浏览器每个会话都要点一次
「高级 → 继续前往」，演示观感不好。

**这个决定的依据是一次补充实测**——它把先前的结论修正了：

```text
8.1 节的诊断是在**客户端开着 VPN 代理**的情况下做的。
关掉 VPN 后重测：明文 HTTP **30/30 全部正常，零异常**。
→ 改写响应的中间设备在 VPN 的出口路径上，不在这条 IP 直连路径上。
```

> **值得记下来的不是"要不要上 TLS"，而是结论是怎么被修正的。**
> 8.1 节说"必须上 HTTPS"，那是当时证据下的正确结论；
> 换一条网络路径再测一次之后，"必须"就变成了"当前不必"。
> 如果当时把结论当成永久事实，就会一直背着一个不必要的复杂度。
> **结论要跟着证据走。**

**当前状态与回滚路径**：

| 项 | 现状 |
| --- | --- |
| Nginx | **单一 HTTP server 块**，全部业务直接在 :80 提供；443 段已移除 |
| 协议选择 | `.env` 的 `SITE_SCHEME`（当前 `http`），由 `lib/common.sh` 的 `site_scheme()` 读取 |
| 证书 | `/etc/ssl/data-platform/server.{crt,key}` 仍在（有效期到 2036），但已不被引用 |
| 恢复 HTTPS | `setup-tls.sh` + 按 commit `347dfdd` 加回 443 段 + `SITE_SCHEME=https` |

**为什么把协议判断从"探测证书"改成"读配置"**：

原来的实现是"有证书就走 https"。停用 TLS 后这个依据就错了 ——
机器上证书还在，于是脚本会继续去测 https，而站点已经不听 443 了。
改成显式配置是因为：**配置表达意图，证书只是产物**。
有证书不等于"现在想用 https"。

**明文形态下的前提（必须记住）**：

> 演示时**不要挂 VPN / 代理**，否则那个偶发 502 可能回来。
> 届时要么关掉代理，要么临时按上表恢复 HTTPS。

