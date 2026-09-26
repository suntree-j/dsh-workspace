# 前端看板（services/web）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 模块：实时数据 Dashboard（Nginx 静态托管 + `/data/api` 反向代理）
> 形态：**零构建**静态单页应用（No-Build SPA）

---

## 1. 这个目录是什么

面向电商实时链路的可视化看板，数据全部来自后端只读查询接口
（Kafka → Flink → Doris 的 ADS 层结果），前端不做任何数据加工与缓存。

```text
浏览器  ──HTTPS──►  Nginx（https://<ip>/data/）   ← TLS 终止在这里
                      ├── /data/            → 本目录静态文件
                      ├── /data/api/*       → 只读数据服务（127.0.0.1:8000）
                      └── /data/agent/*     → 数据问答 Agent（127.0.0.1:8100）
                    :80 只留 /data/healthz 探针，其余 302 跳转到 HTTPS
```

| 文件 | 职责 |
| --- | --- |
| `index.html` | 单页外壳：页面模板（`<script type="text/x-template">`）、导航、挂载点、依赖脚本标签 |
| `app.js` | 应用逻辑：工具函数、API 客户端、ECharts 主题、图表生命周期、hash 路由、6 个页面面板 |
| `styles.css` | 深色数据大屏样式：**设计令牌（:root）** + 外壳布局、指标卡、表格、骨架屏、弹窗、响应式 |
| `README.md` | 本文件：预览、部署、依赖来源与对接约定 |
| `vendor/` | **不存在于 Git**，由部署脚本下载 Vue 3 与 ECharts（见第 4 节） |

### 1.1 六个页面

| 页面 | hash | 内容 |
| --- | --- | --- |
| 总览 | `#/overview` | KPI 卡片（含口径 tooltip 与环比）、交易趋势（GMV 折线 + 订单量柱）、流量趋势、类目 Top5 横向条形 |
| 交易分析 | `#/trade` | GMV / 客单价 / 支付成功率 / 退款率卡片、GMV 与支付金额双轴折线、窗口明细表格（分页） |
| 流量分析 | `#/traffic` | UV / PV 卡片、行为漏斗、转化率卡片与分层流失、PV/UV 趋势 |
| 类目销售 | `#/category` | 类目 GMV 排行条形、占比饼图、明细表格、最近 60 分钟 / 全部切换 |
| 订单明细 | `#/orders` | 类目 + 日期区间筛选、每页 20 条分页、点击行查看订单支付与退款记录 |
| 指标口径 | `#/metrics` | `/api/meta/metrics` 指标字典、`/api/meta/tables` 表结构（可折叠） |

### 1.2 设计系统速览（答辩可对照讲解）

界面质量按"设计系统"落地，而不是逐页调样式。所有规则只引用令牌，不写裸色值：

```text
styles.css :root         颜色 / 间距(4·8·12·16·24·32) / 圆角 / 阴影 / 字号阶梯 / 过渡时长 / 图表高度
styles.css  @media        ≥1440px 三列 · 1024~1440px 两列 · <1024px 单列 + 侧栏抽屉
app.js     CHART_THEME   ECharts 的网格线 / 坐标轴 / 图例 / 提示框 / 调色板（六个页面共用）
app.js     makeLine/makeBar/makeRankBar + moneyAxis/intAxis/timeAxis/rankAxis
```

三条关键设计决策：

1. **信息层级**：KPI 数字是视觉主角（`--fs-kpi`，字重 600、字距 -0.4px），
   单位退到 12px 的次级色，口径说明放在 `?` 提示里 —— 顶部三段（品牌 + 页面标题 + 数据时间/刷新）、
   左侧导航、内容区面板三段式，读者一眼能定位"我现在在哪、数据是什么时候的"。
2. **层级用"极低对比描边 + 表面色递进"表达**，而不是重投影：
   深色大屏上大面积阴影会糊成一片（`--line-subtle` 仅 10% 透明度，
   只在真正浮起的元素上用 `--shadow-2/3`）。
3. **涨跌不靠颜色单独表达**：环比徽标同时给出 ▲/▼ 字符、百分比数字、
   `title` 与 `sr-only` 文字，色盲用户与读屏用户都能得到同样信息。

`--fs-kpi` 之所以"按一行放几张卡"给定字号（而不是用 `vw` 计算）：
grid 轨道是 `minmax(0, 1fr)`，而 `.kpi__value` 是 `nowrap`，
所以能否放下的判据是 **卡片内容宽 ≥ 金额文本宽**。
实测 1600px 视口下内容区宽 1220px：4 列每卡 398px（内容 374px），
而 `1,283,809.29 元` 在 20px 字号下需要 373px —— 4 列刚好卡在临界点上，
因此 ≥1440px 一律 3 列（每卡约 532px，余量充足），
1024~1440px 两列，<1024px 两列，<560px 单列。

---

## 2. 本地预览

### 2.1 准备依赖

前端运行时的两个库放在 `vendor/`，由部署脚本下载（仓库不提交第三方压缩包）：

```bash
bash scripts/install-web.sh
```

脚本会把 `vue.global.prod.js`（Vue 3 全局构建）与 `echarts.min.js` 落到本目录的 `vendor/` 下。
**脚本不执行时页面不会白屏**，而是显示：

```text
前端依赖未就绪，请先执行 bash scripts/install-web.sh
```

### 2.2 启动静态服务器

本项目不使用 npm，用任意静态文件服务器即可（必须通过 HTTP 打开，
直接双击 `index.html` 会出现 `file://` 协议下的相对路径与接口跨域问题）：

```bash
# 方式一：Python（推荐，服务器与本机都有）
cd services/web
python -m http.server 8088

# 方式二：Node（若已安装 Node.js）
cd services/web
npx --yes serve -l 8088 .
```

浏览器访问：<http://127.0.0.1:8088/#/overview>

### 2.3 本地要能看到真实数据，需要两步

1. **接口可达**：把 `index.html` 里的 `<meta name="api-base">` 临时改成本地后端地址
   （例如 `content="http://127.0.0.1:8000"`），构造出与线上一致的接口前缀；
2. **后端就好绪**：确认后端健康检查通过：

   ```bash
   curl http://127.0.0.1:8000/health
   ```

> 只做界面走查（不看数据）时，可以跳过第 2 步：页面会显示红色错误条
> 「无法连接到后端数据接口…」，这是**预期行为**，说明错误处理生效而不是白屏。

### 2.4 只校验静态资源

```bash
# 确认资源引用全部是相对路径，没有被替换成域名根路径
grep -nE '(src|href)=' services/web/index.html
```

---

## 3. 部署到服务器（Nginx）

### 3.1 上传文件

```bash
# 在服务器上准备目录
sudo mkdir -p /opt/data-platform/web/vendor

# 从本地推送（示例：腾讯云服务器）
scp services/web/index.html services/web/app.js services/web/styles.css \
    services/web/README.md  <user>@<ip>:/opt/data-platform/web/
```

`vendor/` 两个库体积较大，建议**在服务器上**执行下载脚本，而不是走 scp：

```bash
ssh <user>@<ip>
cd /opt/data-platform && bash scripts/install-web.sh
```

### 3.2 Nginx 站点配置

> **以文件为准**：站点配置的唯一权威是
> [`deploy/nginx/data-platform.conf`](../../deploy/nginx/data-platform.conf)，
> 由 `scripts/deploy-web.sh` 安装并 reload。下面只是**结构与意图的说明**，
> 不要照抄去改线上配置（少了 TLS 段会让站点起不来）。

要求：页面挂在 `https://<ip>/data/` 下，接口挂在 `https://<ip>/data/api/` 下；
两者必须同源，否则需要额外处理跨域。

```nginx
# 详见 deploy/nginx/data-platform.conf（这里只画结构）
server {                       # :80 —— 只做探针与跳转
    listen 80 default_server;
    location = /data/healthz { return 200 "nginx ok\n"; }
    location / { return 302 https://$host$request_uri; }
}

server {                       # :443 —— 业务入口
    listen 443 ssl http2 default_server;   # 本项目 nginx 1.24.0 的写法
    ssl_certificate     /etc/ssl/data-platform/server.crt;
    ssl_certificate_key /etc/ssl/data-platform/server.key;

    location /data/                { alias /opt/data-platform/services/web/; try_files $uri $uri/ /data/index.html; }
    location /data/api/            { proxy_pass http://127.0.0.1:8000/; }
    location = /data/api/query     { proxy_pass http://127.0.0.1:8000/query; }   # POST（Agent 专用）
    location /data/agent/          { proxy_pass http://127.0.0.1:8100/; }
}
```

> `listen 443 ssl http2;` 而不是 `http2 on;`：后者是 nginx **1.25.1+** 的语法，
> 在 1.24.0 上会让 `nginx -t` 报 `unknown directive`。详见
> [`docs/sprint/SPRINT_6.md`](../../docs/sprint/SPRINT_6.md) 第 8.5 节。

校验与重载：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 3.3 部署后自检

```bash
# 自签证书，本机自检加 -k
curl -kI https://<ip>/data/                     # 期望 200，Content-Type: text/html
curl -ks  https://<ip>/data/api/health          # 期望 {"data":{"status":"ok",...}}
curl -s -o /dev/null -w '%{http_code}\n' http://<ip>/data/   # 期望 302（跳转到 HTTPS）
```

浏览器打开 `https://<ip>/data/`，首次会提示证书不受信任，点「高级」→「继续前往」，
然后逐个切换左侧页面确认图表与表格有数据。

### 3.4 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 页面显示「前端依赖未就绪」 | `vendor/` 下缺少两个 js | 在服务器执行 `bash scripts/install-web.sh`，并在浏览器 Network 面板确认两个文件返回 200 |
| 页面显示「无法连接到后端数据接口」 | 后端未启动或 Nginx 未转发 `/data/api/` | 先 `curl /data/api/health`，再检查 `location /data/api/` 配置 |
| 图表空白但表格有数据 | 容器被 `v-show` 隐藏时 `clientWidth=0` | 已修复：`bindChart` 先挂 `ResizeObserver` 再判尺寸，并有上限重试兜底，见第 6 节第 2 条 |
| 静态资源 404、地址变成域名根路径 | 用了绝对路径 `/app.js` | 本目录所有引用均为 `./` 相对路径，请勿改成根路径 |
| 比率列显示 `—` | 后端按口径返回 `null`（分母为 0） | **正常**，见第 5.2 节；不要改成 0 |

---

## 4. 依赖来源

| 依赖 | 版本要求 | 来源 | 引入方式 |
| --- | --- | --- | --- |
| Vue 3 | 3.4+ 全局构建 | `https://unpkg.com/vue@3/dist/vue.global.prod.js` | `./vendor/vue.global.prod.js`，`window.Vue` |
| Apache ECharts | 5.4+ | `https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js` | `./vendor/echarts.min.js`，`window.echarts` |

约定与理由：

1. **不使用 npm / vite / webpack**，不使用 ES module `import`：毕业设计部署环境只需
   Nginx + 静态文件，避免为了一个看板引入 Node 构建链。
2. **运行时不引用任何 CDN、字体、图标或图片**：内网/断网环境也能打开；
   图标全部为 `index.html` 中的内联 SVG，favicon 未指定以免产生额外请求。
3. `vendor/` 不入 Git（第三方压缩包），由 `scripts/install-web.sh` 在部署阶段下载并校验。

---

## 5. 与后端接口的对接约定

### 5.1 统一信封

所有接口返回 `{ data, source, generated_at }`；错误返回 HTTP 4xx/5xx +
`{ error: { code, message, detail } }`。`app.js` 的 `apiGet()` 负责：

- 拼接 query（`undefined` / `null` 参数自动丢弃）；
- 取信封中的 `data` 交给页面，业务代码不再判断信封结构；
- 4xx/5xx 时抛出携带后端**中文 message** 的异常，最终显示为页面顶部红色错误条；
- 网络不可达时给出独立文案（「无法连接到后端数据接口…」），便于区分"后端没起"与"参数写错"。

API 基址来自 `<meta name="api-base" content="/data/api">`，JS 侧回退为相对路径 `./api`。
**不拼接 `location.origin`**，因此换成任意子路径部署都无需改代码。

用到的端点：

```text
GET /health
GET /overview?window_limit=60
GET /metrics/trade?limit=200
GET /metrics/traffic?limit=60
GET /metrics/category?limit=10&window_limit=1440
GET /funnel?window_limit=1440
GET /orders?limit=20&offset=0[&category=][&start=][&end=]
GET /orders/{order_id}
GET /meta/metrics
GET /meta/tables
```

### 5.2 空值（`null`）约定

| 类别 | 字段 | 后端返回 | 前端显示 |
| --- | --- | --- | --- |
| 比率类 | `payment_success_rate` / `refund_rate` / `click_rate` / `cart_rate` / `buy_rate` / `avg_order_amount` | 分母为 0 时 `null` | **`—`**（不是 0，也不是 NaN） |
| 可加类 | `gmv` / `*_amount` / `*_cnt` / `uv` / `pv` | 无事件时 `0` | `0` / `0.00` |

实现方式：统一的 `formatMoney` / `formatInt` / `formatRate` 在入口处判定
`null` / `undefined` / `''` 并返回 `—`，页面模板不做二次判断；
图表里的 `null` 保持为空洞（`connectNulls: false`），不会把"无数据"画成"0 值"。

### 5.3 金额与时间

- **金额一律按字符串处理**（后端 DECIMAL）。展示用千分位 + 2 位小数，
  且按字符串补零而非 `toFixed`，避免大额金额被浮点截断；
  需要跨行累加时先转成**整数分**（`BigInt`）再运算，全程不出现 `parseFloat` 累加。
- **时间字段为 `"YYYY-MM-DD HH:MM:SS"` 字符串**，图表 X 轴只用 `HH:MM`（`shortTime()`），
  表格与弹窗展示完整时间。

### 5.4 口径提示（tooltip）

总览 KPI 卡片上的「?」提示取自 `/meta/metrics` 的 `definition` 字段；
该接口不可用时回退到 `sql/metadata/metrics.md` 的内置文案，
保证看板不会出现空白提示，也不会与权威口径冲突。

### 5.5 数据来源徽标

顶栏的绿色小圆点徽标取自响应信封的 `source` 字段：
- 字符串 → 直接作为标签；
- 对象 → 取 `note` 作标签，`tables` 作悬停详情（本项目后端实际返回该形态）。

鼠标悬停可以看到接口基址与涉及的 ADS 表清单。
**看板必须能自证"这份数据从哪来"**，所以这个字段不再被丢弃。

### 5.6 KPI 环比的口径（诚实优先）

卡片底部的环比徽标只比较**最近两个窗口**，并且要求两个窗口的取值都非 0；
否则显示「待下一窗口」而不是 `-100%`。
原因：实时链路里最新窗口常常尚未写满（甚至为 0），
直接算环比会得到一个"看起来像事故"的假信号 —— 宁可少给一个数字，也不给一个误导性数字。
悬停徽标可以看到参与比较的两个窗口时间与数值。

---

## 6. 代码结构导览（`app.js`）

```text
依赖就绪检查        window.Vue / window.echarts 缺失 → 中文提示，不白屏
常量与元信息        API_BASE、图表调色板 C、CHART_THEME（ECharts 主题集中处）
工具函数            formatMoney / moneyToCents / formatInt / formatRate / buildDelta / shortTime …
API 客户端          apiGet(path, params) → 返回 data；错误抛 ApiError（中文 message）；解析 source
指标口径缓存        preloadDefinitions() 一次性拉取 /meta/metrics
图表生命周期        renderChart(el, option) 统一渲染；bindChart(getEl, buildOption) 负责 init/resize/dispose
页面通用状态        usePanel()：loading、error、updatedAt、run()、setReload()
路由                ROUTES + hashchange → currentPage（URL 可分享、可回退）
公共组件            Icon / Skeleton / EmptyState / KpiCard / SelectBox
页面面板            Overview / Trade / Traffic / Category / Orders / Metrics
根组件              App：侧栏（含抽屉）、顶栏（数据时间 + 刷新）、错误条、页面切换
```

图表样式集中在两处，页面只描述"画什么"：

```text
CHART_THEME    网格线 / 坐标轴 / 图例 / 提示框 / 调色板 / animation:false
makeLine       折线：1.8px 圆头描边 + 自上而下渐隐面积（静态也成立，不依赖动画）
makeBar        柱状：四角圆角、收窄宽度、低透明度（深色底上不刺眼）
makeRankBar    类目排行：横向渐变条 + 右侧数值标签
moneyAxis/intAxis  金额轴只保留整数千分位（刻度带 ".00" 会挤掉绘图区宽度）
rankAxis       类目轴：名称靠右、超长截断（width + overflow:'truncate'）
```

关键实现说明：

1. **页面切换即销毁**：`<overview-panel :key>` 等组件用 `v-if` + `key` 强制卸载，
   `onBeforeUnmount` 中统一 `dispose()` 图表实例，避免长时间挂机后的内存泄漏。
2. **图表自适应与"首屏容器不可见"**：`bindChart` 的渲染顺序是
   **取元素 → 先挂 `ResizeObserver`（幂等）→ 再判断尺寸 → init/重绘**。
   之所以强调"先挂观察器"，是因为图表容器用 `v-show` 控制显隐：首屏数据未到达时
   `display:none`，`clientWidth/clientHeight` 都是 0。若此时直接 `return`，
   观察器就没机会注册，等容器变为可见时既不会 init 也不会 resize —— 图表永远空白
   （本项目实测踩过：`draw()` 被调用 8 次，每次都因尺寸为 0 早退，从未走到 `echarts.init`）。
   另外 `onMounted` 与 `watch(..., { flush: 'post' })` 都可能早于"移除 `display:none`"
   的那次 DOM 补丁，因此读取尺寸统一推迟到 `Vue.nextTick` 之后。
   重试有快慢两条路径且都有上限：`requestAnimationFrame` 最多 30 帧，
   定时退避最多 40 次（约 16 秒）；渲染成功后立即停止，不会变成无限轮询。
   容器尺寸变化（侧栏折叠、窗口缩放）时由 `ResizeObserver` 触发 resize + 重绘，
   避免 ECharts 沿用首次 init 的旧尺寸。
   **改 UI 时不要退化成"没尺寸就 return"。**
3. **数据倒序展示**：`/metrics/trade` 按 `window_start` 升序返回（便于画折线），
   表格展示时前端倒序，符合"最新在前"的阅读习惯。
4. **刷新按钮**：调用各页面登记的 `reload`，页面内部有 loading 互斥，重复点击无副作用。
5. **空态与加载态**：加载中显示骨架（KPI 用 `variant="kpi"` 占位成卡片形状，
   避免加载完成时跳版）；无数据时显示"图标 + 说明 + 下一步建议"，
   而不是留一块空白画布（图表容器与空态互斥，不会叠在一起）。
   错误态显示后端返回的中文 `message` 与 `detail`，并给出重试按钮。
6. **交互与可访问性**：导航用 `<a href="#/...">` 支持中键新开标签；
   订单行 `tabindex="0"` 可用回车打开详情，弹窗打开时焦点移入、关闭时归还触发元素；
   自绘下拉具备 `aria-expanded` / `aria-haspopup` / `role="listbox"`；
   图标按钮一律带 `aria-label`（视觉文字用 `.sr-only` 提供）；
   选中态除颜色外还有左侧指示条，涨跌除颜色外还有 ▲/▼ 字符；
   键盘 `:focus-visible` 轮廓统一 2px 主强调色。

---

## 7. 自检清单（改动本目录后请过一遍）

```text
[ ] node --check app.js                                           通过
[ ] 资源引用全部是 ./ 相对路径，无外部 CDN / 字体 / 图片
[ ] 无 console.log 调试残留，无 TODO / Lorem 占位文案
[ ] 文件为 UTF-8 无 BOM、LF 行尾
[ ] vendor 缺失时显示中文提示而不是白屏
[ ] 比率为 null 时显示 —，可加指标显示 0
[ ] 每个页面每个可见 .chart 容器内 canvas ≥ 1（浏览器控制台执行：
    [...document.querySelectorAll('.chart')].filter(c => c.style.display !== 'none')
      .every(c => c.querySelector('canvas'))）
[ ] 切换页面后图表实例被 dispose（开发者工具 Memory 面板不持续增长）
[ ] Nginx 挂在 https://<ip>/data/ 下访问正常，接口走 /data/api/；http 访问返回 302
[ ] 窄屏（<1024px）侧栏抽屉可开可关，放大回桌面后不残留遮罩
```
