# Frontend Final Review

> 改造对象：`intelligent-data-platform/services/web`
> 设计依据：`docs/thesis/FRONTEND_UI_AUDIT.md`、`docs/thesis/FRONTEND_IMPLEMENTATION_PROMPT.md`
> 实施方式：**零构建 SPA**（Vue 3 全局构建 + ECharts + `index.html` x-template + `app.js` + `styles.css`），
> 未引入 npm / Vite / Webpack / UI 组件库 / CDN / 外部字体
> 完成日期：2026-09-27
> 截图：`docs/thesis/ui-audit-screenshots/after/`（改造后）、`docs/thesis/ui-audit-screenshots/`（改造前）

---

## 1. Before

审计（`FRONTEND_UI_AUDIT.md` §3）记录的改造前状态，逐条对应到实测现象：

| 维度 | 改造前 |
| --- | --- |
| 技术栈 | Vue 3.5.13 全局构建 + ECharts 5.6.0 + 单文件 `styles.css`；**无构建链** |
| 路由 | 8 个 hash 路由挤在一个"数据看板"标题下的平铺导航里 |
| 视觉层级 | 6 张 KPI 卡**同字号同样式**，GMV 5,189 万与"退款 254 笔"视觉等权 |
| 首屏信息 | 顶栏之下**立刻**是 KPI 卡 —— 读者不知道这是什么系统、链路是否健康 |
| 对账 | 结论埋在一条 `notice` 里，要滚过 6 张离线 KPI 才看到 |
| 双链路对比 | 4 行表格，读者需自己逐行比对才能得出"两边相等" |
| Ask 页 | 回答**原文显示 `## 结论` 与 `| 表 |` 管道符**；steps 默认全展开甩 JSON；无执行轨 |
| 顶栏 | 数据源徽标是一整句工程说明（`source.note`），挤压"数据时间 + 刷新" |
| 工程文案 | `panel__desc` 直接写 `/api/meta/metrics`、`/api/metrics/category` 等接口路径 |
| "待下一窗口" | 6 张卡同时出现彩色 pill，形成灰色徽标矩阵，看起来像 6 处异常 |
| 实测缺陷 | **`ALL_WINDOWS = 1000000` 违反 API 的 `le=20160` → 类目"全部"选项 422** |

**改造前截图**：`ui-audit-screenshots/01…08-*.png`（8 张，1920×1080，真实访问线上）。

---

## 2. After

### 2.1 总览：从"KPI 墙"变成"驾驶舱"

自上而下五层，每层解决一个具体问题：

| 层 | 内容 | 解决什么 |
| --- | --- | --- |
| A. IdentityRow | 系统名 + 一句话定位 + 4 枚技术标签 + **3 枚状态芯片**（数据服务 / 批流对账 / Agent） | 10 秒内说清"这是什么系统、现在健康吗" |
| B. Hero KPI | GMV（`--fs-kpi-hero` 28px）\| 批流一致性（PASS + 比对窗口 + 不一致数） | 拉开一级/二级层级；把核心差异化能力与 GMV 同级 |
| C. Secondary KPI | 订单量 / 支付金额 / 退款金额 / PV | 保持原有信息量，`kpi-grid--overview` 显式 4 列 |
| D. Trends | 交易趋势（全宽）+ 流量趋势 \| 类目 Top5 | 不变 |
| E. EntryCards | 数据问答 / 离线与对账 / 系统监控（Grafana 外链） | 把深一层的能力提到主内容区 |
| F. 明细快照 | 原生 `<details>` 默认折叠 | 与类目 Top5 信息重复，默认展开属冗余 |

**实测取证（真实 DOM 文本，2026-09-27 21:31）：**

```
chips: ["数据服务Doris 正常 · 32 ms", "批流对账PASS · 不一致 0", "Agent就绪 · langgraph"]
hero : ["GMV（下单金额）51,890,375.77元 最近 11,459 个交易窗口（分钟级）累计 待下一窗口",
        "批流一致性（实时 vs 离线）PASS 比对窗口 11,458 · 不一致 0"]
```

### 2.2 侧栏：分组信息架构（不新增路由）

```
概览        · 总览
实时分析    · 实时链路 / 交易分析 / 流量分析 / 类目销售 / 订单明细
离线与可信  · 离线与对账
AI          · 数据问答
治理        · 数据质量 / 指标口径 / 系统监控（外链 Grafana）
```

组标题用 `role="presentation"` —— 它是纯视觉分组节点，不让键盘 Tab 序列多出 5 个无语义的停留点。
折叠态下组标题隐藏，改由**组间分隔线**承担分组职责，避免变成 8 个孤立图标。

### 2.3 离线与对账：结论变一级，双塔让"相等"结构可见

```
┌─ Reconcile Hero（PASS 徽标 + 4 个指标卡 + 静默窗口说明）──────────┐
│  PASS  批流对账一致   比对窗口 11,458 │ 一致 11,458 │ 不一致 0     │
├─ Twin Compare ─────────────────────────────────────────────────┤
│  实时链路（Kafka → Flink → Doris）  vs  离线链路（Spark → Iceberg → Doris）│
│  GMV 51,890,375.77                       GMV 51,890,375.77      │
├─ 逐项对比表（保留差异列，供逐项核对）───────────────────────────┤
├─ 离线 KPI → 按天趋势 → 离线类目 Top10 ─────────────────────────┤
```

> **静默窗口必须写出来**：交易域 11,458 个比对窗口里有 5,504 个两侧 GMV 均为 0，
> 它们**计入 matched 但不是独立证据**。页面显式说明这一点，
> 否则评委会以为"没数据却判 PASS"。具体数字（5504/5954）前端拿不到该拆分，
> 因此只做**定性说明，不编造具体值**。

### 2.4 Agent：从"表单问答"变成"证据链 workflow"

新增 6 段**执行轨**，每段都来自服务端真实返回的 `graph.path` / `steps` / `executed_sql`：

```
提问 → 口径检索 → 工具取数 → 只读 SQL → 结果校验 → 回答
```

**实测（真实提问「这些数据准不准？实时和离线一致吗？」，约 12s 返回）：**

| 段 | 状态 | 说明 |
| --- | --- | --- |
| 提问 | done | 收到问题 |
| 口径检索 | done | 命中 5 条口径 |
| 工具取数 | done | 2 次成功 |
| 只读 SQL | done | 2 条已执行 |
| 结果校验 | done | 通过 |
| 回答 | done | 11.2s |

**回答正文改为安全 Markdown 渲染**，实测渲染结果：

| 项 | 数量 |
| --- | --- |
| 标题元素 `.md-h` | 4 |
| 表格元素 `.md-table` | 1 |
| 粗体 `<strong>` | 11 |
| 行内代码 `.md-code` | 21 |
| **Markdown 残留（`##` / `\| ---`）** | **false** |

**XSS 防护**：`renderMarkdown` 只用 Vue 的 `h()` 生成 vnode，**全程不碰 `innerHTML`、不使用 `v-html`**，
因此模型输出里的 HTML 标签会被当纯文本显示。全仓库 `v-html` 命中数 = **0**。

**执行过程默认折叠**：每步只显示 `tool` / 成功与否 / 一行摘要（来自 `step.summary` 的真实 `row_count` 与 `elapsed_ms`），
展开才显示工具入参 JSON —— 原先默认全展开，像调试台而不是产品 trace。

**不展示 chain-of-thought**：`graph.trace` / `plan` / system prompt / 密钥一概不渲染。

### 2.5 新增两页

| 路由 | 内容 | 数据来源 |
| --- | --- | --- |
| `#/realtime` | 实时链路健康：最新窗口与滞后、窗口密度、本窗口取值、近 60 窗口趋势 | `GET /metrics/trade`、`GET /metrics/traffic`（**已有接口**） |
| `#/quality` | 25 条校验清单（按域分组 + 筛选）、实时完整性信号、只读权限边界、运行方式 | `checks.conf` 静态镜像 + `GET /batch/reconcile` + `GET /meta/tables` |

> **与审计原文的偏差（已由项目负责人确认放行）**：
> `FRONTEND_UI_AUDIT.md` §2 / §10 与实施任务书硬约束 6 都写"**不新建** Quality 完整页"。
> 项目负责人明确要求新增这两个独立页面，并选定"**只展示真实可得的字段**"这一档。
> 因此本页**不显示任何 PASS / FAIL / 通过率** —— 数据服务没有质量校验接口，
> 前端算不出来，写死就是伪造。页面第一屏就写明这一点，并给出 CLI 命令。
> 这与硬约束 4（禁止伪造）一致，与硬约束 6 的字面表述冲突，**以项目负责人的决定为准并在此留痕**。

### 2.6 Design Tokens（扩展，未重建）

`styles.css :root` 是既有的设计令牌体系（颜色/间距/圆角/阴影/字号/过渡），本次**只补齐缺口**：

| 新增令牌 | 值 | 用途 |
| --- | --- | --- |
| `--ai-accent` | `#7c9cff` | AI 域强调（Ask 步骤轨、证据区、IdentityRow 的 AI 标签）。全站**只有**这两处用，保证"看到这个色 = AI 相关"的映射不被稀释 |
| `--ai-soft` / `--ai-line` | 12% / 34% 透明度 | 同上，填充与描边 |
| `--fs-kpi-hero` | `28px` | 一级主指标，每页最多 1~2 个 |

间距仍只用 `4/8/12/16/24/32`；未新增散落裸色值。

---

## 3. Visual Changes

### 3.1 层级

| 改动 | Before | After |
| --- | --- | --- |
| KPI 字号 | 全部 20–24px 同级 | `--fs-kpi-hero` 28px（一级）/ 20–22px（二级） |
| KPI 网格 | 基础 `.kpi-grid` 3 列通吃 | 变体各自显式声明：`--hero` 2 列、`--overview` 4 列、`--4` 4 列 |
| 面板色条 | 每张 KPI 顶部 2px 域色 | 保留；一级面板标题左侧 3px primary 条 |
| 视觉层级 | 字号 + 位置 | **字号 + 分组 + 位置**三重表达 |

### 3.2 状态与语义色

| 元素 | 设计 |
| --- | --- |
| 状态芯片 | 圆点 + 文字双通道；`ok` 绿 / `warn` 黄 / `bad` 红 / **`unknown` 中性灰** |
| `unknown` 为什么不是红 | "没取到"与"确实失败"是两件事，把没取到画成红会制造假警报 |
| 对账 Hero | 左边框 3px + 徽标底色 + PASS/FAIL 大字，三重表达 |
| 漏斗断点 | 断点级左侧 warning 竖条 + 独立提示块 |
| 执行轨 | `done` 实心绿点 / `fail` 红点 / **`skip` 空心点**（未发生≠失败）/ `retried` AI 紫蓝描边 |

### 3.3 文案降噪

| Before | After |
| --- | --- |
| 顶栏徽标 = `source.note` 整句工程说明 | **短标签**「只读数据服务 · N 张表」；完整 note + 表清单移入 `title` |
| `指标口径来自 /api/meta/metrics` | `悬停或聚焦「?」可查看口径定义；完整字典见「指标口径」页` |
| `按 GMV 降序；切换时间范围会重新请求 /api/metrics/category` | `按 GMV 降序；切换时间范围或排行条数会重新聚合` |
| `来源：/api/meta/tables，共 N 张表` | `数据服务实际放行的表，共 N 张表` |
| 6 张卡同时出现彩色「待下一窗口」pill | 降级为**一行灰字**（`.kpi__pending`），判据用 `delta.pending` 标记而不是字符串匹配 |

### 3.4 动效

沿用既有策略：只保留 120–200ms 反馈类过渡（hover / 焦点 / 折叠）。
新增的唯一动画是加载态执行轨首段的呼吸（`railPulse` 1.4s），
**已在 `prefers-reduced-motion: reduce` 下统一关闭**（沿用文件末尾既有的全局规则）。
无飞入、无粒子、无 glow、无无限 spin。

---

## 4. Functional Verification

### 4.1 语法与静态检查

| 检查 | 结果 |
| --- | --- |
| `node --check services/web/app.js` | **exit 0** |
| `styles.css` 花括号平衡 | **500 / 500** |
| `v-html` / 危险 `innerHTML` 命中 | **0**（仅框架级 `bootEl.innerHTML` 与根模板读取） |
| 新增 npm 依赖 / 构建工具 / CDN | **0** |

### 4.2 逐页真实浏览器检查（Edge headless，CDP 驱动）

10 条路由 × 3 档视口，订阅 `Runtime.consoleAPICalled` / `Runtime.exceptionThrown` / `Log.entryAdded`：

```
=== 1920x1080 / 1440x900 / 1280x800 ===
overview / realtime / trade / traffic / category / orders / batch / ask / quality / metrics
→ console errors = 0     exceptions = 0     warnings = 0
```

**注意**：本地预览走了一个本地反向代理（见 §4.4），因此 `#/ask` 也能在预览里真实提问成功。

### 4.3 修复的两类**真实缺陷**

| # | 缺陷 | 证据 | 处置 |
| --- | --- | --- | --- |
| 1 | **类目"全部"选项恒 422** —— `ALL_WINDOWS = 1000000` 违反 API 契约 `window_limit: Query(0, ge=0, le=20160)` | 服务器日志：`GET /metrics/category?limit=50&window_limit=1000000 → 422 Unprocessable Entity`；直接 `curl` 对比 `window_limit=0 → 200` | 改用后端自己的哨兵值 **`0`**（语义=全量口径）。**不改成 20160** —— 那会把"全部"悄悄变成"最近 14 天" |
| 2 | **顶栏在 1280 下横向撑破** —— 整页溢出 **35px** | headless 真实测量：`header.topbar clientWidth 969 < scrollWidth 1028` | 给 `.topbar__left/__right` 加 `min-width: 0`；把不可控长度的 `source.note` 包进 `.src-badge__note` 单行截断 |

> 缺陷 1 的**隐性**才值得记：类目"全部"失败会走错误分支（页面照常渲染），
> 而订单页的类目候选项拉取失败后被 `.catch` 静默兜底成空数组，
> 表现只是"下拉里没有可选项"—— 很容易被当成"数据就是这样"。

### 4.4 验证方法本身的两次纠错（如实记录）

| 问题 | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 控制台把**线上标签页**的报错当成预览页的 | 反复看到 `window_limit=1000000` 的 422，而预览代码里已无该值 | CDP 脚本取 `/json/list` 的**第一个** target，那可能是真实浏览器里打开的线上站点 | 改用 `/json/new` **新建** target，并只监听它 |
| 溢出检测**大面积假阳性** | 27 行"问题"，全是 `.sr-only` / `.tip` / 表格 | 判据用了 `scrollWidth > clientWidth` —— 对**会换行的文本块**是假信号；且只看元素自身、没看祖先是否是横向滚动容器 | 改判据为"子元素 border box 右边界越过父元素右边界"，并排除祖先含 `overflow-x: auto` 的情况 |

> 两次都不是被测代码的问题，而是**验证工具的判据错了**。
> 这类错误的方向都是"把正常状态报成故障"，代价是让人去改一个不存在的问题。

### 4.5 响应式（真实测量，非目测）

```
=== 1920x1080 ===  overflow=0 全部 10 页；hero=2列 overview=4列 entry=3列 twin=3列 grid4=4列
=== 1440x900  ===  overflow=0 全部 10 页；hero=2列 overview=4列 grid4=4列
=== 1280x800  ===  overflow=0 全部 10 页；hero=2列 overview=2列 grid4=2列 entry=2列
problem rows: 0        console errors: 0
```

图表 canvas 逐页存在（overview 3 / traffic 2 / batch 2 / realtime 1 / trade 1），无渲染失败。

---

## 5. Responsive Verification

| 断点 | 侧栏 | Hero | 二级 KPI | 入口卡 | 双塔 | 对账 Hero |
| --- | --- | --- | --- | --- | --- | --- |
| ≥1440（1920/1440） | 248 常驻 | 2 列 | 4 列 | 3 列 | 3 列（RT\|vs\|BT） | 状态 + 4 指标并排 |
| 1024–1439 | 248 常驻 | 2 列 | 2×2 | 2 列 | 3 列 | 上下堆叠 + 指标 2 列 |
| ≤1023 | 抽屉 | 2 列 | 2 列 | 2 列 | 3 列 | 上下堆叠 |
| ≤767 | 抽屉 | 1 列 | 2 列 | 1 列 | **竖排** | 指标 2 列 |
| ≤560 | 抽屉 | 2 列 | 1 列 | 1 列 | 竖排 | 指标 2 列 |

**答辩主场景 1920×1080 与 1440×900：两档均 0 溢出、0 控制台错误。**

---

## 6. Remaining UI Limitations

| # | 限制 | 说明 |
| --- | --- | --- |
| 1 | **`#/quality` 不显示每条校验的通过情况** | 数据服务没有质量结论的只读接口。真实结论只能通过 `bash scripts/run-quality-checks.sh` 取得。本页展示的是清单 + 可真实取得的完整性信号，**不做推测** |
| 2 | **`#/realtime` 不设新鲜度阈值** | 只如实显示"滞后约 N 分钟"。阈值属于告警策略（Prometheus 侧），前端不代替它下结论 |
| 3 | **窗口数在两处相差 1** | 首页 Hero 显示 `11,459`（实时 ADS 整表行数，分钟表最新），对账卡显示 `11,458`（对账批次的比对窗口数）。两者**同源不同口径**，已在 `EVIDENCE_MATRIX` P1-9 记为已知口径差；页面未强行抹平 |
| 4 | **不做并发/压力下的前端表现** | 与项目整体一致：从未做并发测试 |
| 5 | **本地预览依赖一个代理** | Nginx 给 `/data/api/` 加了 CORS 头，但 `/data/agent/` **没有**（见下条），因此本地跨域预览 Ask 页需要本地代理。线上同源访问不受影响 |
| 6 | **`/data/agent/` 缺 CORS 头（后端不一致，未改）** | 实测 `/data/api/health` 返回 `access-control-allow-origin: *`，`/data/agent/health` **不返回**该头。API 应用内有 `CORSMiddleware(allow_origins=["*"])`，Agent 侧没有。属 `deploy/nginx/**` 与 `services/agent/**` 的改动，**超出本次"只改前端三文件"的边界**，故只记录不改 |
| 7 | **未做视觉回归基线** | 截图存放在 `ui-audit-screenshots/after/`，但未接入像素级 diff 工具（会引入 npm 依赖，违反硬约束） |
| 8 | **`#/metrics` 仍是宽表** | 审计建议"表头 sticky + 公式列 code + 行高 44"已生效；7 列字典表在 1280 下仍需横向滚动（由 `.table-wrap` 承担，属有意设计） |

---

## 7. 交付物与验收对照

### 7.1 改动文件（严格限于允许范围）

| 文件 | 变化 |
| --- | --- |
| `services/web/index.html` | 14 → **16** 个模板（新增 `tpl-quality` / `tpl-realtime`）；分组侧栏；KPI hero variant；Batch 重组；Ask 重写证据区；3 个新图标 + **兜底图标** |
| `services/web/app.js` | 新增 `QualityPanel` / `RealtimePanel` / `MarkdownView`；`renderMarkdown`（vnode，无 innerHTML）；`buildRail`；`probeStatus`；`QUALITY_GROUPS` 镜像；修 `ALL_WINDOWS`；修 `data.latest` 层级 |
| `services/web/styles.css` | 新增 §17–19 三节样式（对账 Hero / 双塔 / 执行轨 / Markdown / SQL 卡 / 质量页）；修响应式列数覆盖；修顶栏收缩 |
| `tests/test_frontend_quality_manifest.py` | **新增**（55 项）：前端质量清单与 `checks.conf` 逐条比对（id / title / domain / engine） |
| `docs/thesis/ui-audit-screenshots/after/` | **新增** 13 张改造后截图（10 张 1920 + 3 张 1440） |

### 7.2 未改动（硬约束）

`services/api/**`、`services/agent/**`、`services/mcp/**`、`sql/**`、数据库、Flink / Spark / Doris / Iceberg、
Docker 业务配置、任何 API 请求/响应契约 —— **全部未改**。

### 7.3 验收清单对照（实施任务书 §Acceptance Checklist）

| 项 | 结果 |
| --- | --- |
| `#/overview` 10 秒内能看出：系统定位、健康状态、GMV、对账摘要 | ✅ 实测 DOM 文本已取证 |
| 无伪造涨跌幅 | ✅ 无真实字段处不显示趋势；「待下一窗口」如实降级为灰字 |
| `#/batch` 有 Realtime vs Offline 双塔，数字与接口一致 | ✅ 双塔 GMV `51,890,375.77` == 接口值 |
| `#/ask` Markdown 可读；有步骤轨；有 SQL/表证据；无 CoT | ✅ 4 标题/1 表格/11 粗体/21 行内码；6 段轨；2 张 SQL 卡；0 CoT |
| 侧栏分组完成；Grafana 外链可用 | ✅ 5 组；外链 `target="_blank" rel="noopener noreferrer"` |
| 未改后端任何契约 | ✅ |
| 无新构建工具、无 CDN | ✅ |
| 1920/1440/1280 无横向撑破；图表 canvas 存在 | ✅ 三档 0 溢出；canvas 逐页存在 |
| vendor 缺失时仍显示中文引导 | ✅ 未改 `app.js` 的 boot 兜底分支 |

---

## 8. 一句话结论

**前端已从"功能完整的数据后台"提升到"专业的数据平台驾驶舱"**：
首屏能自证"这是什么系统 + 现在健康吗"，对账结论与双链路对比成为结构可见的一级信息，
Agent 页把"回答"变成了带执行轨与可核对 SQL 的**证据链**，
并且三档答辩视口下 **0 横向溢出、0 控制台错误**。

**达到毕业答辩展示级，停止继续修改。**
