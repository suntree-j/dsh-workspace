# Frontend UI Audit

> 审计对象：`intelligent-data-platform/services/web`  
> 审计方式：**真实访问** `http://36.151.150.140/data/#/...` + 对照源码（`index.html` / `app.js` / `styles.css`）  
> 审计时间：2026-09-27  
> 截图目录：`docs/thesis/ui-audit-screenshots/`  
> 约束：**本阶段不改后端 API / Agent / SQL / 数据库**；只产出前端视觉改造方案。

---

## 0. Preview Evidence（真实预览结论）

### 0.1 环境事实

| 项 | 实测结果 |
| --- | --- |
| 页面入口 | `http://36.151.150.140/data/#/overview` |
| API | `GET /data/api/health` → 200；Doris 只读账号 `agent_ro` 正常 |
| Agent | `GET /data/agent/health` → 200；LLM `deepseek-flash`；检索 66 docs |
| Grafana | `http://36.151.150.140/grafana/` → 200（**不在本 SPA 内**） |
| 前端依赖 | 审计开始时线上 `vendor/` 缺失 → 页面显示「依赖未就绪」；已补齐 `vue.global.prod.js` + `echarts.min.js` 后方可预览 |
| 数据 | Overview / Trade / Traffic / Category / Orders / Batch / Ask / Metrics **均能拿到真实后端数据**（偶发 HTTP 502，属服务瞬时问题，不是 UI 伪造） |

### 0.2 已保存截图

| 文件 | 页面 | 视口 |
| --- | --- | --- |
| `01-overview-1920.png` | 总览（有 KPI + 图表） | 1920×1080 |
| `02-trade-1920.png` | 交易分析 | 1920×1080 |
| `03-traffic-1920.png` | 流量分析（漏斗有数据） | 1920×1080 |
| `04-category-1920.png` | 类目销售 | 1920×1080 |
| `05-orders-1920.png` | 订单明细（6000 条） | 1920×1080 |
| `06-batch-1920.png` | 离线与对账（PASS，11458 窗口一致） | 1920×1080 |
| `07-ask-answer-1920.png` | 数据问答（真实回答 + SQL + steps） | 1920×1080 |
| `08-metrics-1920.png` | 指标口径 | 1920×1080 |

### 0.3 实测关键业务数字（用于 UI 层级设计，禁止伪造趋势）

- Overview GMV：`51,890,375.77`；订单 `6,000`；支付 `5,287`；退款 `254`；UV `1,200`；PV `20,000`
- Batch 对账：`比对窗口 11,458`，`不一致 0`，`is_pass=1`；Realtime GMV = Offline GMV
- Agent 示例问「这些数据准不准」：12.1s，2 轮 `sql_query`，表 `ads_reconcile_summary` / `ads_reconcile_trade_1m`
- KPI 环比：当前多为「待下一窗口」（最新窗口为 0，**诚实行为，禁止改成假 ↑12.5%**）

---

## 1. Current Frontend Stack

| 维度 | 现状 | 说明 |
| --- | --- | --- |
| 框架 | **Vue 3.5.13**（`vue.global.prod.js`） | 全局构建，`window.Vue.createApp`，**无 npm / Vite / Webpack** |
| UI 组件库 | **无** Element Plus / Ant Design | 自研：`Icon` / `Skeleton` / `Empty` / `Kpi` / `SelectBox` + 原生表单 |
| 图表 | **ECharts 5.6.0** | `CHART_THEME` 集中在 `app.js`；`animation:false` |
| CSS | **单文件设计令牌** `styles.css` | `:root` tokens + BEM 类名；已有 spacing 4/8/12/16/24/32 |
| 路由 | **Hash 路由** | `#/overview` … `#/metrics`；`ROUTES` 写在 `app.js` |
| 模板 | `index.html` 内 `<script type="text/x-template">` | 零构建 SPA |
| API | `apiGet` / `apiPost` | 信封 `{ data, source, generated_at }`；基址 `<meta name="api-base" content="/data/api">`；Agent 基址由 `/api` → `/agent` 推导 |
| 部署 | Nginx 静态托管 `/data/` | `vendor/` 不进 Git，`scripts/install-web.sh` 下载 |

**架构结论（给实施 Agent）：**  
继续保持 **No-Build SPA**。改造只改 `services/web/{index.html,app.js,styles.css}`，不要引入构建链，不要换 UI 库。

---

## 2. Current Route Map

| Hash | 标题 | 数据 API | 前端模板 id | 是否存在 |
| --- | --- | --- | --- | --- |
| `#/overview` | 总览 | `/overview`, `/metrics/trade|traffic|category` | `tpl-overview` | ✅ |
| `#/trade` | 交易分析 | `/metrics/trade` | `tpl-trade` | ✅ |
| `#/traffic` | 流量分析 | `/metrics/traffic`, `/funnel` | `tpl-traffic` | ✅ |
| `#/category` | 类目销售 | `/metrics/category` | `tpl-category` | ✅ |
| `#/orders` | 订单明细 | `/orders`, `/orders/{id}` | `tpl-orders` | ✅ |
| `#/batch` | 离线与对账 | `/batch/overview`, `/batch/reconcile` | `tpl-batch` | ✅ |
| `#/ask` | 数据问答 | `/agent/health`, `POST /agent/ask` | `tpl-ask` | ✅ |
| `#/metrics` | 指标口径 | `/meta/metrics`, `/meta/tables` | `tpl-metrics` | ✅ |
| `#/quality` | 数据质量 | — | — | ❌ **无前端页**（质量在 `infrastructure/quality` + CLI） |
| `#/monitor` | 系统监控 | — | — | ❌ **无前端页**（Prometheus/Grafana 独立） |

**导航改造原则：** 只重排/重命名现有 8 个路由；**不新建不存在的业务页**。质量与监控用「外链入口」或 Overview 状态条承接，而不是伪造页面。

---

## 3. Current Visual Problems

### 3.1 全局问题（所有页面）

1. **第一印象偏「深色后台模板」，不是「数据平台驾驶舱」**  
   - 侧栏品牌「批」+ 长标题存在，但主内容区顶栏立刻变成「面包屑 / 页面名 / 工程说明」；10 秒内缺少「系统定位 + 运行健康」层。  
   - 顶栏 `src-badge` 实测文案接近「口径唯一权威来源：sql/metadata/metrics.md…」，像文档引用，不像数据源状态。

2. **信息层级扁平**  
   - KPI 数字字号接近（`--fs-kpi` 约 20–24px），GMV 与「退款笔数」同权。  
   - `panel__desc` 大量暴露接口路径、公式、实现细节（答辩可讲，但不应作为默认主文案）。

3. **工程注释污染产品文案**  
   - 例：Overview「指标口径来自 `/api/meta/metrics`」；Category「切换会重新请求 `/api/metrics/category`」；Orders「DWD 明细层…limit/offset」。  
   - 目标：实现细节进 tooltip / `?` / metrics 页；面板标题旁只留业务一句话。

4. **「待下一窗口」徽标噪声**  
   - 6 张 KPI 同时灰徽标「待下一窗口」，视觉上像系统异常。  
   - 这是正确业务逻辑（最新窗口为 0 不算环比），但应降级为 tertiary 文案或只在 hover 显示。

5. **Sidebar 信息架构扁平**  
   - 8 个入口全挂在「数据看板」下，看不出 Realtime / Lakehouse / AI / Governance 结构。

6. **Markdown 未渲染（Ask 页严重）**  
   - 回答正文直接显示 `## 结论`、`| 表 |` 管道符；证据区有价值，但答案可读性差。

7. **偶发 API 502 时错误条正确出现**  
   - 错误态可用；但错误条占满顶栏下方，且空态同时刷满多面板，显得「整站崩溃」。应保留错误条，面板继续 skeleton / last-good（若无缓存则 skeleton，不连环 empty）。

### 3.2 `#/overview`（截图 `01-overview-1920.png`）

**Current：** 核心指标 6 卡（3×2）→ 交易趋势（折柱）→ 流量趋势 | 类目 Top5 → 明细表。

**Problems（具体）：**

1. 缺「系统是什么」层：无项目一句话定位、无批流一致性状态、无 Agent/监控入口。  
2. 6 卡等权：GMV `51.8M` 与退款 `254` 同尺寸、同卡片样式。  
3. 每张卡底部「待下一窗口」重复 4–6 次，形成灰条矩阵。  
4. 顶栏 source badge 过长，挤压「数据时间 + 刷新」。  
5. 「明细快照」与上方类目图信息重复（同类目 GMV），价值偏低。  
6. 无 Realtime vs Offline 迷你对比（数据在 `/batch/reconcile` 已有，但首页未用）。

### 3.3 `#/trade`（`02-trade-1920.png`）

**Problems：**

1. 4 卡等权；GMV 合计应是一级，支付成功率/退款率是二级。  
2. `panel__desc` 直接写「整数分口径重算」——应收到 KPI tip。  
3. 趋势图 OK；窗口明细表列过多（12 列），在 1440 下横向压力大。  
4. 「待下一窗口」仍出现在 GMV 卡。

### 3.4 `#/traffic`（`03-traffic-1920.png`）

**Problems：**

1. UV「2 人」与说明「跨窗口不可加」正确，但与大数字并置时易被误解为全站 UV——需要更强 secondary context。  
2. 漏斗图有价值；转化率区 4 小卡 + steps 列表略挤，购买=0 时整体转化全 0，应突出「漏斗断点」而不是 4 个相同 0.00%。  
3. 底部趋势图与 Overview 流量趋势重复度高（可接受，但样式需统一）。

### 3.5 `#/category`（`04-category-1920.png`）

**Problems：**

1. 排行条 + 饼图 + 合计卡 + 表，四段重复同一 TopN，略冗余。  
2. 工具条文案暴露 API path。  
3. 紫色排行条与交易蓝、流量青并存，域色可用，但饼图调色板偏「默认 ECharts 六色」，略跳。

### 3.6 `#/orders`（`05-orders-1920.png`）

**Problems：**

1. 筛选区日期控件占位 `mm/dd/yyyy`（浏览器原生），与中文产品气质不符。  
2. 会员等级/省份大量 `—`，表格「空列感」强；可把空列降权或提供列显隐（不改 API）。  
3. 顶栏 source 文案工程化。  
4. Modal 详情结构可用，视觉普通。

### 3.7 `#/batch`（`06-batch-1920.png`）——项目答辩核心页

**Current 优点：** 顶部绿色 notice「批流对账一致 · 比对窗口 11458 · 不一致 0」信息正确。

**Problems：**

1. Realtime vs Offline 只是表格四行，缺少「双塔 KPI」视觉（Realtime | Offline | Δ）。  
2. 离线 6 卡再次与 Overview 同构，没有突出「对账结论」为一级信息。  
3. 日期展示 `10-25 ~ 09-26` 易读性一般，应完整 `YYYY-MM-DD HH:mm`。  
4. zero-zero / business-silent 窗口说明缺失（后端若有 matched 含双侧 0，应在 secondary 注释，避免评委以为「没数据却 PASS」）。  
5. 差异明细表仅 mismatch 时出现——PASS 时页面中下段偏空，可展示「最近比对摘要卡」。

### 3.8 `#/ask`（`07-ask-answer-1920.png`）——核心展示点

**Current：** 输入框 + 示例 chip + 回答面板 + 表标签 + SQL pre + steps 列表。

**Problems：**

1. 仍是「表单问答」，不是「Agent workflow」。  
2. 回答区 Markdown 未渲染（`##`、表格管道原样显示）。  
3. steps 把整段 JSON arguments 甩在 `<pre>`，像调试台，不像产品 trace。  
4. 缺少固定步骤轨：Query → Retrieve → Tool → SQL → Validate → Answer（后端已有 steps/tables/sql，足够做安全 trace，**不要展示 chain-of-thought**）。  
5. 加载态只有一行文字进度，没有步骤动画/指示。  
6. 空态大块留白，适合补「能力说明 + 示例场景」但不加霓虹。

### 3.9 `#/metrics`（`08-metrics-1920.png`）

**Problems：**

1. 字典表 7 列墙字，padding 偏紧，扫描困难。  
2. 公式列未用 code chip 样式区分。  
3. 作为 Governance 页价值高，但侧栏位置靠后且无分组标签。

### 3.10 缺失页（记录，不伪造）

| 能力 | 现状 | UI 策略 |
| --- | --- | --- |
| Data Quality（25 checks） | CLI `run-quality-checks.sh` | **本轮不新建假页面**；Overview/Batch 增加「质量入口说明」或外链文档；若未来有只读 API 再做 |
| Monitoring | Grafana `/grafana/` | SPA 内增加顶栏/侧栏 **外链**「系统监控」，不复制 Grafana |

---

## 4. Target Visual Direction

**定位一句话：**  
Enterprise Analytics Console for a **Lakehouse + Realtime + AI Agent** platform — 专业、克制、可信、适合答辩与作品集截图。

| 要 | 不要 |
| --- | --- |
| 中性深色表面层级（已有 token 可沿用微调） | 赛博朋克大屏 / 霓虹 / 粒子 / 大面积 glow |
| 一个主品牌蓝 + 语义色 + AI accent | 每张卡一种颜色 |
| 清晰 hierarchy：状态 → 核心 KPI → 对比 → 趋势 → 明细 | 六块白/深色矩形同等大数字 |
| 诚实二次信息（窗口、来源、更新时间） | 伪造 ↑12.5% |
| 轻量 hover / 160ms transition / skeleton | 飞入、闪烁、无限动画 |

**气质关键词：** Data Platform · Observability · Trust (reconcile) · AI with evidence

---

## 5. Design System

> 以现有 `styles.css :root` 为基线，**统一命名并补齐缺口**；禁止散落裸色值。

### 5.1 Colors（Token 映射）

| Token（目标名） | 现有变量 | 值（保持/微调） | 用途 |
| --- | --- | --- | --- |
| `--bg` | `--bg` | `#080c15` | 页面底 |
| `--bg-side` | `--bg-side` | `#0a101c` | Sidebar |
| `--surface` | `--surface-1` | `#101725` | Panel |
| `--surface-elevated` | `--surface-2` | `#16202f` | KPI / 表头 |
| `--surface-overlay` | `--surface-3` | `#1b2739` | 下拉/悬停 |
| `--border` | `--line` | `rgba(148,163,184,.16)` | 默认边 |
| `--border-subtle` | `--line-subtle` | `.10` | 内分割 |
| `--primary` | `--accent` | `#4c8dff` | 主按钮/导航 |
| `--success` | `--pos` | `#46c46a` | PASS / 正向 |
| `--warning` | `--warning` | `#e2b04a` | WARNING |
| `--danger` | `--neg` | `#ff7a72` | FAIL / 错误条 |
| `--text-primary` | `--text-1` | `#eef3f9` | 标题/KPI |
| `--text-secondary` | `--text-2` | `#c3cddb` | 正文 |
| `--text-muted` | `--text-3` | `#94a2b6` | 辅助 |
| `--ai-accent` | **新增** | `#7c9cff`（低饱和，非紫霓虹） | Ask 页步骤轨/标签 |

域色保留：`--domain-trade/traffic/category`，**仅作 3px 左边条或图标色**，不要整卡染色。

### 5.2 Typography

| 角色 | Token | 规格 |
| --- | --- | --- |
| 页面 H1 | `--fs-22` / fw 600 | 顶栏标题 |
| 面板 H2 | `--fs-16` / fw 600 | panel title |
| KPI 一级数字 | `--fs-kpi-hero: 28px` | **仅 Overview/Batch 主指标 1–2 个** |
| KPI 默认数字 | `--fs-kpi: 22px` | 二级卡 |
| KPI 三级 | `--fs-18` | 辅助卡 |
| 正文 | `--fs-14` | |
| 辅助 | `--fs-12` | sub / meta |
| 等宽 | `--font-mono` | SQL、ID、时间 |

数字：`font-variant-numeric: tabular-nums`；KPI `letter-spacing: -0.02em`。

### 5.3 Spacing

只用：`4 / 8 / 12 / 16 / 24 / 32`（已有 `--sp-1…6`）。  
禁止新增 13/17/21/27px。  
Panel padding：`24px`；KPI 内边距：`16px`；grid gap：`12` 或 `16`。

### 5.4 Radius / Shadow / Icon / Button / Card / Table / Chart

| 元素 | 规格 |
| --- | --- |
| Radius | 控件 6、卡片 10、大面板 14（已有） |
| Shadow | 仅 dropdown / modal / tooltip 用 `--shadow-2/3`；卡片默认无重阴影 |
| Icon | 继续内联 SVG `tpl-icon`；新增 `status`/`spark`/`shield` 如需 |
| Button | Primary / Ghost / Icon；高度 `--control-h: 32px`；禁用降低 opacity |
| Card / Panel | `surface-1` + `1px border`；标题左侧 3px primary bar **仅一级面板** |
| Table | 表头 `surface-2`；行高 40；数字列右对齐；hover 行 `surface-3` |
| Chart | 沿用 `CHART_THEME`；网格线更淡；双轴注释放 legend 不放长 desc |

---

## 6. Page-by-page Redesign

### 6.1 Overview

| | |
| --- | --- |
| **Current** | 6 等权 KPI → 趋势 → 双图 → 表 |
| **Problem** | 10 秒说不清系统；无健康/对账；技术文案；KPI 无主次 |
| **Target** | 见 §7 |
| **Exact Change** | 改 `tpl-overview` + Overview 组件数据装配；新增 `StatusStrip` / `HeroKpi` / `ReconcileMini` 组件（纯前端，数据来自现有 `/overview` + `/batch/reconcile` + `/agent/health`） |

### 6.2 Trade

| | |
| --- | --- |
| **Current** | 4 KPI + 双轴趋势 + 宽表 |
| **Problem** | 文案工程化；列过多 |
| **Target** | GMV 宽卡（跨 2 列）+ 3 个次级卡；desc 缩为 tip；表默认隐藏次要列（支付失败等）到「更多列」toggle（前端状态即可） |
| **Exact Change** | `tpl-trade` 网格 class；`cards` 结构调整；CSS `kpi--hero` |

### 6.3 Traffic

| | |
| --- | --- |
| **Current** | 规模 4 卡 + 漏斗 + 转化 + 趋势 |
| **Problem** | 0 转化时四卡同噪 |
| **Target** | 漏斗为视觉中心；转化区改为「断点高亮」：购买=0 时第三步用 warning 左边条 + 文案「断点：加购→购买」 |
| **Exact Change** | `tpl-traffic` steps 增加 class；KPI tip 强化 UV 口径 |

### 6.4 Category

| | |
| --- | --- |
| **Current** | 条形 + 饼 + 合计 + 表 |
| **Problem** | 重复 |
| **Target** | 保留条形 + 表；饼图降为可选（默认显示）；去掉 API path 文案；合计并入表脚或条形标题 meta |
| **Exact Change** | `tpl-category`；饼图 `v-show` 由 seg 控制「占比」 |

### 6.5 Orders

| | |
| --- | --- |
| **Current** | 筛选 + 表 + modal |
| **Problem** | 日期格式；空列；工程 badge |
| **Target** | 筛选条更紧凑（单行 toolbar）；空值显示 muted `—`；modal 分「订单摘要 / 支付 / 退款」三段 |
| **Exact Change** | CSS + `tpl-orders`；不改订单 API |

### 6.6 Batch

| | |
| --- | --- |
| **Current** | notice + 6 KPI + 日趋势 + 对比表 + 类目 |
| **Problem** | 对比不够视觉化；一级信息不是对账 |
| **Target** | 见 §9 |
| **Exact Change** | `tpl-batch` 重排；新增 `CompareTwin` 组件 |

### 6.7 Ask（Agent）

| | |
| --- | --- |
| **Current** | textarea + chips + 回答 + SQL + steps JSON |
| **Problem** | 无 workflow；Markdown 裸奔；steps 像调试 |
| **Target** | 见 §8 |
| **Exact Change** | `tpl-ask` + 轻量 `renderMarkdownSafe()`（仅标题/表格/加粗/代码，**禁止 HTML 原样**）；`StepRail` 映射 `answer.steps` |

### 6.8 Metrics

| | |
| --- | --- |
| **Current** | notice + 宽表 + accordion |
| **Problem** | 表密度过高 |
| **Target** | 表头 sticky；公式列 `code`；域列 chip；行高 44；搜索保留 |
| **Exact Change** | CSS 为主 |

---

## 7. Overview Layout

**目标结构（1920 桌面，自上而下）：**

```
┌─ Topbar：面包屑 | 标题「总览」 | StatusStrip | 数据时间 | 刷新 ─┐
├─ A. IdentityRow（高 64–72）────────────────────────────────────┤
│  左：系统名短标题 + 一句话定位（Lakehouse · 批流一体 · AI Agent） │
│  右：三枚状态芯片（可点击跳转）                                   │
│     [API OK] [对账 PASS · 0 mismatch] [Agent Ready]            │
├─ B. Hero KPI（一级，2+1）───────────────────────────────────────┤
│  [ GMV 大数字 28px + 窗口范围 + 来源 Realtime ADS ]  宽 2 列      │
│  [ 批流一致性：Matched 11458 | Mismatch 0 | PASS ]   宽 1 列      │
├─ C. Secondary KPI（二级，4 卡）──────────────────────────────────┤
│  Orders | Paying Users | Payment Amount | Refund Amount         │
│  每卡：label / value / sub(真实字段) / 无假趋势                    │
│  「待下一窗口」改为 footer muted 或 tip，不占彩色 pill            │
├─ D. Trends（三级）──────────────────────────────────────────────┤
│  交易趋势（全宽，高 320）                                         │
│  流量趋势 | 类目 Top5（各半）                                     │
├─ E. Platform Entry（四级）───────────────────────────────────────┤
│  三个入口卡：数据问答 → #/ask | 离线与对账 → #/batch | 监控 → /grafana/ │
└─ 去掉或折叠「明细快照」表（与类目重复）；需要时放到 Category 页 ─┘
```

**数据来源（禁止造数）：**

| UI 块 | API |
| --- | --- |
| Hero GMV / Secondary KPI | `GET /overview`（已有） |
| 对账芯片 / Hero 一致性卡 | `GET /batch/reconcile`（已有，Overview 增发一次请求） |
| Agent 芯片 | `GET /agent/health`（Ask 页已在用，提到 App 级或 Overview） |
| 图表 | 现有 trade/traffic/category |

**文案替换示例：**

- ❌ `指标口径来自 /api/meta/metrics`  
- ✅ `口径悬停「?」可查看；完整字典见「指标口径」`

---

## 8. Agent UI

**布局（`#/ask`）：**

```
┌─ 左/上：Compose ─────────────────────────────────────────────┐
│  标题：用自然语言问数据（只读）                                 │
│  Textarea + 主按钮「提问」                                      │
│  示例 chips（保留 4 个真实示例）                                 │
├─ 中：StepRail（回答后或加载中显示）─────────────────────────────┤
│  (1) Query  (2) Retrieval*  (3) Tool  (4) SQL  (5) Validation* │
│  (6) Answer                                                     │
│  *Retrieval/Validation：若 steps 无独立工具，用「已执行/已守卫」│
│   状态点根据 steps[].ok 与 executed_sql 是否非空推导            │
├─ 下：Answer Panel ─────────────────────────────────────────────┤
│  Meta：model · elapsed_ms · rounds（保留）                      │
│  Body：安全 Markdown 渲染（h2/h3/table/code/strong/list）       │
├─ Evidence（可折叠，默认展开）──────────────────────────────────┤
│  Tables chips | SQL cards（复制按钮）| 守卫说明一行 muted       │
├─ Trace（默认折叠）─────────────────────────────────────────────┤
│  每步：round badge | tool 名 | ok/fail | purpose 一行摘要       │
│  展开才显示 arguments JSON（现在默认全展开 → 改为折叠）         │
└────────────────────────────────────────────────────────────────┘
```

**禁止：** 展示 LLM chain-of-thought、系统 prompt、密钥。  
**允许：** `steps[].tool` / `ok` / `arguments.purpose` / `executed_sql` / `tables`。

**加载态：** StepRail 逐步 `pending → active → done`，文案沿用现有「识别指标 → …」，不要转盘特效。

---

## 9. Batch/Stream UI

```
┌─ Reconcile Hero Banner（一级）───────────────────────────────┐
│  左大状态：PASS / FAIL（success/danger）                      │
│  右指标：Compared Windows | Matched | Mismatch | Compared At │
│  次要说明：含双侧为 0 的 quiet window 仍计 matched（若产品确认）│
├─ Twin Compare（一级视觉）────────────────────────────────────┤
│  Realtime 卡          vs           Offline 卡                 │
│  GMV / Orders / Pay / Refund（值来自 compareRows）            │
│  底栏 Δ：全 0 显示 success「差异 0」                           │
├─ Offline KPI（二级，可 4 卡：GMV/订单/支付成功率/退款率）─────┤
├─ Daily Trend（三级）─────────────────────────────────────────┤
├─ Category Top（四级）────────────────────────────────────────┤
└─ Mismatch Table：仅 mismatch>0 显示（保持）──────────────────┘
```

---

## 10. Data Quality UI

**本轮范围：**

- **不新建**完整 Quality 页面（无只读 API 契约）。  
- 在 Overview `StatusStrip` 增加静态说明 chip：`质量校验：CLI · 25 checks`，tooltip 指向 `scripts/run-quality-checks.sh` / 论文章节。  
- 若后续后端增加 `GET /quality/summary`，再做：Overview 环 + Check List；**现在不要假数据**。

---

## 11. Responsive Strategy

| 断点 | 策略（与现 CSS 对齐并验收） |
| --- | --- |
| ≥1440 | Sidebar 248；Hero KPI 2+1；二级 KPI 4 列；图表双栏 |
| 1280–1439 | Sidebar 可折叠；KPI 2 列；Hero 堆叠为 1 列 |
| ≤1023 | Sidebar 抽屉；Topbar 留菜单按钮（已有） |
| 答辩主场景 | **1920×1080 / 1440×900 优先** |

验收：无横向 `overflow`；表可横向滚；图表 `ResizeObserver` 逻辑不得回退。

实测 1440×900：sidebar 248、main ~1177、无横向溢出。

---

## 12. Animation Strategy

| 允许 | 禁止 |
| --- | --- |
| hover 背景/边框 160ms | 页面飞入 |
| skeleton shimmer（已有） | 粒子/霓虹呼吸 |
| StepRail 状态切换 | 无限 spin（刷新图标旋转仅 loading 时保留） |
| modal fade | 大面积 glow |

`prefers-reduced-motion` 已有，保持。

---

## 13. Components to Reuse

- `Icon`, `Skeleton`, `Empty`, `Kpi`（扩展 variant）, `SelectBox`
- `CHART_THEME`, `makeLine/makeBar/makeRankBar`
- `apiGet/apiPost`, `formatMoney/Int/Rate`, `buildDelta`
- Shell：`sidebar` / `topbar` / `errorbar` / `pager` / `modal`

---

## 14. Components to Create

| 组件 | 位置 | 职责 | 数据 |
| --- | --- | --- | --- |
| `StatusStrip` | App topbar 或 Overview A | API/对账/Agent 三芯片 | health + reconcile + agent health |
| `HeroKpi` | Overview B | 大号主指标 | overview.kpi |
| `ReconcileMini` | Overview B | PASS + matched/mismatch | batch/reconcile |
| `EntryCards` | Overview E | 3 个能力入口 | 纯前端链接 |
| `CompareTwin` | Batch | Realtime vs Offline | reconcile compare |
| `StepRail` | Ask | 安全执行步骤 | ask response |
| `MarkdownView` | Ask | 安全渲染回答 | answer.answer |
| `SqlCard` | Ask | SQL + 复制 | executed_sql |

---

## 15. Components to Delete / Simplify

| 项 | 动作 |
| --- | --- |
| Overview「明细快照」表 | 删除或默认折叠 |
| 各页冗长 `panel__desc` API 路径 | 删除或移入 tip |
| KPI 彩色「待下一窗口」pill | 降级为 muted 文本 |
| Ask steps 默认展开 JSON | 改为折叠 |
| Category 饼图默认强展示 | 改为可选 |
| 顶栏超长 source 文案 | 改为短标签 + title tooltip 放全文 |

---

## 16. Risk Assessment

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 改坏 API 契约 | 高 | **禁止改** `services/api` / agent / sql |
| Markdown XSS | 中 | 只渲染白名单节点，禁 `v-html` 原始 HTML |
| Overview 增加 reconcile/agent 请求致 502 放大 | 中 | 并行请求 + 单芯片失败不影响 KPI |
| 无构建链，模板巨大 | 低 | 继续 x-template；控制新增组件数量 |
| 线上 `vendor/` 缺失 | 高 | 部署检查列入 Acceptance；`deploy-web.sh` 已有 check |
| 假趋势诱惑 | 高 | 明确禁止；只用真实字段 |
| 引入 UI 库/打包器 | 中 | **禁止** |

---

## 17. Implementation Order

1. **Token / 全局壳**：Sidebar 分组、Topbar source 短标签、文案降噪、KPI variant（hero/secondary）、待下一窗口降级  
2. **Overview 重组**：StatusStrip + Hero + ReconcileMini + EntryCards；去明细表  
3. **Batch CompareTwin**  
4. **Ask StepRail + MarkdownView + SqlCard**  
5. **Trade/Traffic/Category/Orders/Metrics** 精修  
6. **响应式验收** 1920 / 1440 / 1280  
7. **部署**：确保 `vendor/` + `bash scripts/deploy-web.sh`  

---

## 18. Acceptance Criteria

1. 打开 `#/overview`，10 秒内可见：系统名定位、API/对账/Agent 状态、GMV 一级、对账摘要。  
2. 无任何伪造涨跌幅；「待下一窗口」不再形成 6 连彩色噪声。  
3. `#/batch` 有 Realtime vs Offline 双塔 + PASS/FAIL 一级状态；数字与 API 一致。  
4. `#/ask` 回答 Markdown 可读；有 StepRail；SQL/表证据保留；无 CoT。  
5. 不改后端契约；`node --check app.js` 通过；无 CDN。  
6. 1920/1440/1280 无横向撑破；图表有 canvas。  
7. 截图级观感：深色克制、适合答辩；非 ERP 蓝表、非赛博大屏。  
8. 侧栏分组体现：概览 / 实时分析 / 离线与可信 / AI / 治理（仍映射现有 8 路由）。  
9. Grafana 可从 UI 外链打开；不内嵌整站 Grafana。  
10. `vendor/` 缺失时仍显示中文引导（回归）。

---

## Appendix A — Sidebar IA（仅重排标签，不增路由）

```
概览
  · 总览                         → #/overview

实时分析
  · 交易分析                     → #/trade
  · 流量分析                     → #/traffic
  · 类目销售                     → #/category
  · 订单明细                     → #/orders

离线与可信
  · 离线与对账                   → #/batch

AI
  · 数据问答                     → #/ask

治理
  · 指标口径                     → #/metrics
  · 系统监控（外链）             → /grafana/  (target=_blank)
```

## Appendix B — 文件改动边界

| 允许 | 禁止 |
| --- | --- |
| `services/web/index.html` | `services/api/**` |
| `services/web/app.js` | `services/agent/**` |
| `services/web/styles.css` | `sql/**` / Doris / Flink / Spark |
| `services/web/README.md`（同步说明） | 新增 npm 依赖 / Vite |
| 部署时确保 `vendor/` | 伪造 mock 数据层 |

---

**审计结论一句话：**  
前端已是可用的深色数据看板且令牌体系不差，但信息架构与层级仍像「功能演示后台」；通过 Overview 驾驶舱化、Batch 双塔对比、Ask 证据化 workflow，即可达到答辩/作品集级，而无需重写技术栈或后端。

---

# Appendix C — Implementation Result（实施结果回填）

> 实施完成日期：2026-09-27
> 完整交付报告：**`docs/thesis/FRONTEND_FINAL_REVIEW.md`**（含 Before/After、功能验证、响应式验证、遗留限制）
> 改造后截图：`docs/thesis/ui-audit-screenshots/after/`（10 张 1920×1080 + 3 张 1440×900）
>
> 本节只回填"**审计结论哪些被采纳、哪些没有、为什么**"，设计细节不在此重复。

## C.1 已采纳并完成

| 审计条目 | 实施情况 |
| --- | --- |
| §5 Design System（补 `--ai-accent`、`--fs-kpi-hero`，间距仍只用 4/8/12/16/24/32） | ✅ 补齐两个令牌，未新增散落裸色值 |
| §6.1 / §7 Overview 重组（IdentityRow + StatusStrip + Hero + Secondary + EntryCards + 折叠明细表） | ✅ 全部落地 |
| §6.6 / §9 Batch（Reconcile Hero + CompareTwin + 静默窗口说明） | ✅ 全部落地 |
| §6.7 / §8 Ask（StepRail + 安全 Markdown + SqlCard + steps 折叠） | ✅ 全部落地；`v-html` 命中数 = 0 |
| §6.2–6.5 / §6.8 其余页精修 | ✅ Trade 拆 hero；Traffic 漏斗断点；Category / Orders / Metrics 文案降噪 |
| §14 Components to Create（StatusStrip / HeroKpi / ReconcileMini / EntryCards / CompareTwin / StepRail / MarkdownView / SqlCard） | ✅ 全部实现；`ReconcileMini` 实际并入 Overview 的 `reconcileCard`，未单独建组件（见 C.2） |
| §15 Components to Delete / Simplify（去明细表默认展开、去 API 路径文案、降级「待下一窗口」、steps 折叠、顶栏 source 短标签） | ✅ 全部执行 |
| Appendix A Sidebar IA（5 组 + Grafana 外链，不增路由的**分组**部分） | ✅ 落地；折叠态用组间分隔线接替组标题 |
| §11 Responsive Strategy（1920 / 1440 / 1280） | ✅ 三档真实测量，**0 横向溢出、0 控制台错误** |
| §12 Animation Strategy（只保留反馈类过渡，reduced-motion 全关） | ✅ 新增动画仅执行轨加载呼吸，已被既有全局 reduced-motion 规则覆盖 |
| §16 风险：Markdown XSS | ✅ 用 Vue `h()` 生成 vnode，**不使用 `v-html`**，模型输出中的 HTML 按纯文本显示 |
| §16 风险：Overview 增发请求致 502 放大 | ✅ `probeStatus` 三条探针各自 catch，单条失败只让**它自己那枚芯片**变灰 |
| §16 风险：假趋势诱惑 | ✅ 无真实字段处不显示趋势 |
| §18 Acceptance Criteria 1–10 | ✅ 逐条见 `FRONTEND_FINAL_REVIEW.md` §7.3 |

## C.2 **未采纳**的设计建议（含原因）

| 审计条目 | 原建议 | 实际处置 | 原因 |
| --- | --- | --- | --- |
| §2 导航改造原则 / §10 / 任务书硬约束 6 | "**不新建**不存在的业务页；Quality 用外链或状态条承接；**禁止**新建 Quality 完整页" | **新增了 `#/quality` 与 `#/realtime` 两个独立页面** | **项目负责人明确要求新增，并选定"只展示真实可得的字段"这一档。** 为同时守住硬约束 4（禁止伪造），`#/quality` **不显示任何 PASS/FAIL/通过率** —— 数据服务没有质量校验接口，前端算不出真实结论；页面第一屏即写明这一点并给出 CLI 命令。`#/realtime` 的阈值判据同理交给 Prometheus。**这是本次唯一一处与审计原文冲突的改动，以此留痕。** |
| §14 `ReconcileMini` | 单列一个组件 | 并入 Overview 的 `reconcileCard` 计算属性 | 它只是一张 KPI 卡 + 一个字段读取，单独建组件会让"读同一份 `statusProbe`"的逻辑分散两处；审计 §13 自己也写着"优先复用、不要建几十个新组件" |
| §3.1 第 7 条 | "偶发 502 时应保留错误条，面板继续 skeleton / last-good，不连环 empty" | **未改** | 现有实现已满足"单页失败保留上一次成功数据"（各 panel 的 `run(..., 失败回调)`），未复现"连环 empty"；无实测缺陷则不动 |
| §6.3 Traffic | "转化区改为断点高亮；KPI tip 强化 UV 口径" | 部分采纳 | 断点高亮**已实现**（`funnelBreak` + warning 左边条）。UV 口径文案**未再加长** —— 现有 KPI `tip` 已写明"COUNT(DISTINCT user_id)、跨窗口不可加"，再加会让 tip 过长反而没人读 |
| §6.5 Orders | "把空列降权或提供列显隐（不改 API）" | **未做** | 空值已统一显示 muted `—`。列显隐是**交互功能而非视觉优化**，会扩大改动面与回归风险；答辩场景下"少一列"的收益低于风险 |
| §6.4 / §15 Category | "饼图降为可选（默认显示）" | **保留默认显示** | 实测该页信息密度不高，饼图与排行条回答的是不同问题（占比 vs 排名），强行隐藏会让页面变空 |
| §15 "顶栏超长 source 文案改为短标签" | 改为短标签 | ✅ 已做，但**做法与建议不同** | 建议暗示可直接截断容器；实测**对 `inline-flex` 外层容器设 `max-width` 无效，且会破坏父级收缩计算（反而多溢出 511px）**。正确做法是把长文本包进专门的 `span`（`.src-badge__note`）再截断 |

## C.3 实施中发现并修复的**非视觉缺陷**（审计未覆盖）

| # | 缺陷 | 证据 | 处置 |
| --- | --- | --- | --- |
| 1 | 类目「全部」选项恒 **422**：`ALL_WINDOWS = 1000000` 违反 API 契约 `window_limit: Query(0, ge=0, le=20160)` | 服务器日志 `GET /metrics/category?limit=50&window_limit=1000000 → 422`；`window_limit=0 → 200` | 改用后端哨兵值 `0`（语义=全量）。**不改 20160**，那会把"全部"悄悄变成"最近 14 天" |
| 2 | 顶栏在 1280×800 下**整页横向溢出 35px** | headless 实测 `header.topbar clientWidth 969 < scrollWidth 1028` | 子块加 `min-width: 0` + 长文本单行截断 |
| 3 | 响应式列数**互相覆盖**：媒体查询里 `.kpi-grid` 与 `.kpi-grid--4` 同列声明，同特异性下后者胜出，把 4 列压回 3 列（表现为"4 张卡排成 3+1、第二行空两格"） | 截图 + `gridTemplateColumns` 实测 | 媒体查询内基础 `.kpi-grid` 与各变体**各自显式声明列数**，消除覆盖 |
| 4 | Overview 对账芯片/卡片**恒显示"未取得"**：字段读错层级（结论在 `data.latest.*`，不是 `data.*`） | `curl /batch/reconcile` 实测 `data` 顶层只有 `latest/totals/deltas/mismatches` | 改读 `data.latest`；修复后 DOM 取证：`批流对账PASS · 不一致 0` |
| 5 | `/data/agent/` **缺 CORS 头**（后端两服务不一致） | 实测 `/data/api/health` 返回 `access-control-allow-origin: *`，`/data/agent/health` 不返回 | **只记录未改**：属 `deploy/nginx/**` 与 `services/agent/**`，超出"只改前端三文件"的边界 |

> 缺陷 4 值得单记：它**不报错**，只是静默显示兜底文案 ——
> 而同一份数据在 Batch 页（读 `.latest`）显示正常。
> 这类"取错层级"只能靠**跨页面对比实测**发现，读代码很难看出来。

## C.4 未采纳项的共同判据

三条通用判据，供后续维护参考：

1. **能实测出缺陷的才改**（缺陷 1–5 都有可复现证据）；"应该更好看"不构成改动理由。
2. **改动面与收益要相称**：列显隐、饼图可选这类**交互功能**改动，
   在"只做视觉与信息架构优化"的范围内收益不足，一律不做。
3. **不为了满足设计稿而破坏已有功能**：Category 饼图、UV 长 tip 两处均保留了现有行为，
   并在上表写明理由，而不是"照做"。

