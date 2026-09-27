# FRONTEND_IMPLEMENTATION_PROMPT

> 把本文完整交给 DeepSeek Harness / Coding Agent 执行。  
> 设计细节的唯一权威：`docs/thesis/FRONTEND_UI_AUDIT.md`  
> 截图参考：`docs/thesis/ui-audit-screenshots/`

---

## Role

你是前端实现 Agent。你的任务是：**只改前端视觉与信息架构**，把 `services/web` 提升到「现代化、专业、克制的数据平台产品感」，适合毕业答辩与作品集截图。

你不是后端工程师，不是数据工程师。

---

## Hard Constraints（违反即失败）

1. **禁止修改**：`services/api/**`、`services/agent/**`、`services/mcp/**`、`sql/**`、数据库、Flink/Spark、Docker 业务配置、任何 API 请求/响应契约。  
2. **禁止引入**：npm、Vite、Webpack、Element Plus、Ant Design、CDN、外部字体包。  
3. **保持**：Vue 3 全局构建 + ECharts + 零构建 SPA（`index.html` x-template + `app.js` + `styles.css`）。  
4. **禁止伪造**：任何 ↑↓ 百分比趋势；无真实字段就不要显示趋势。  
5. **禁止**：赛博朋克大屏、霓虹、粒子、大面积 glow、夸张进场动画。  
6. **禁止**：新建不存在后端的 Quality 完整页或复刻 Grafana。  
7. **允许改动文件**：  
   - `services/web/index.html`  
   - `services/web/app.js`  
   - `services/web/styles.css`  
   - 必要时更新 `services/web/README.md`  
8. 先读 `docs/thesis/FRONTEND_UI_AUDIT.md` 再写代码；不要重新发明视觉方向。

---

## Current Stack（勿改技术选型）

- Vue 3.5.x global：`./vendor/vue.global.prod.js`  
- ECharts 5.x：`./vendor/echarts.min.js`  
- Hash routes in `app.js` `ROUTES`  
- API base：`<meta name="api-base" content="/data/api">`  
- Agent base：由 API base 推导 `/data/agent`

---

## Implementation Goals

### Goal A — Global Shell

1. Sidebar 按审计 Appendix A 分组（仅 UI 分组，路由 key 不变）：  
   概览 / 实时分析 / 离线与可信 / AI / 治理  
2. 增加外链「系统监控」→ `/grafana/`（`target="_blank"` `rel="noopener"`）。  
3. Topbar `src-badge` 改为短标签（如「Doris · ADS」或 `source.note` 截断 24 字），完整信息放 `title` tooltip。  
4. 清理各页 `panel__desc` 中的 API path / 实现细节；细节进 KPI `tip` 或 metrics 页。  
5. KPI 增加 variant：  
   - `kpi--hero`：字号 28px，可跨 2 列  
   - 默认二级  
   - 「待下一窗口」改为 `.kpi__foot--muted` 文本，去掉强样式 pill（逻辑 `buildDelta` 可保留）

### Goal B — Overview（最重要）

按审计 §7 重组 `tpl-overview`：

1. **IdentityRow**：一句话定位 + StatusStrip  
   - API：已有 health（可复用 app 级）  
   - 对账：`GET /batch/reconcile` → PASS/FAIL、mismatch 数  
   - Agent：`GET` agent `/health` → ready / not ready  
2. **Hero**：GMV 大卡 + 批流一致性卡（matched/mismatch/pass）  
3. **Secondary KPI**：订单、下单用户、支付金额、退款金额（字段来自现有 overview）  
4. **Trends**：保留交易趋势、流量趋势、类目 Top5  
5. **EntryCards**：跳转 Ask / Batch / Grafana  
6. **删除或默认隐藏**「明细快照」表  

Overview 可并行请求 reconcile/agent；单个失败只灰芯片，不影响 GMV。

### Goal C — Batch（`#/batch`）

按审计 §9：

1. Reconcile Hero Banner 强化 PASS/FAIL  
2. 新增 **CompareTwin**：Realtime vs Offline（用现有 compare 数据）  
3. Offline KPI 可减到 4 个主指标  
4. 保留日趋势与类目  
5. secondary 文案说明：matched 可含业务静默窗口（双侧 0），**不是异常**  
6. 不改 reconcile API

### Goal D — Ask（`#/ask`）

按审计 §8：

1. 新增 **StepRail**（Query → Tool → SQL → Answer；可用 steps 推导状态）  
2. **安全 Markdown 渲染**回答（支持标题/表格/加粗/列表/行内代码；**禁止**任意 HTML）  
3. SQL 用 **SqlCard**（等宽 + 复制按钮）  
4. steps 默认只显示 tool/ok/purpose，JSON arguments **折叠**  
5. 不展示 chain-of-thought  
6. 加载态驱动 StepRail，不要粒子动画

### Goal E — Other pages（精修）

- Trade：GMV hero 宽卡；表可「精简列/全部列」前端 toggle  
- Traffic：购买=0 时高亮漏斗断点  
- Category：弱化饼图或改为可选；去 API 文案  
- Orders：筛选 toolbar 更紧；modal 分段标题  
- Metrics：表头 sticky、公式 code 样式、行距加大  

### Goal F — Design Tokens

在 `styles.css :root` 对齐审计 §5：补 `--ai-accent`、`--fs-kpi-hero`；继续只用 4/8/12/16/24/32 spacing。

---

## Non-Goals

- 不写 Quality 假页面  
- 不内嵌 Grafana iframe  
- 不改 Agent 提示词或工具  
- 不升级为 TypeScript / SFC 工程  

---

## Implementation Order（必须按序）

1. tokens + shell（sidebar 分组、badge、kpi variant、文案降噪）  
2. Overview 重组  
3. Batch CompareTwin  
4. Ask StepRail + Markdown + SqlCard  
5. 其余页精修  
6. 1920 / 1440 / 1280 自检  
7. `node --check services/web/app.js`  
8. 部署时确认 `vendor/` 存在（`bash scripts/deploy-web.sh` 或等价检查）

---

## Acceptance Checklist（做完必须自检）

- [ ] `#/overview` 10 秒内能看出：系统定位、健康状态、GMV、对账摘要  
- [ ] 无伪造涨跌幅  
- [ ] `#/batch` 有 Realtime vs Offline 双塔，数字与接口一致  
- [ ] `#/ask` Markdown 可读；有步骤轨；有 SQL/表证据；无 CoT  
- [ ] 侧栏分组完成；Grafana 外链可用  
- [ ] 未改后端任何契约  
- [ ] 无新构建工具、无 CDN  
- [ ] 1920/1440/1280 无横向撑破；图表 canvas 存在  
- [ ] vendor 缺失时仍显示中文引导  

---

## Deliverables

1. 代码改动仅限 `services/web`（+ 可选 README）  
2. 在 PR/提交说明中列出：改了哪些模板/组件、验证了哪些路由截图级结果  
3. **不要**修改 `docs/thesis/FRONTEND_UI_AUDIT.md` 的设计结论（除非发现实测冲突，单独注明）

---

## Start Command（给你自己）

```text
1. Read docs/thesis/FRONTEND_UI_AUDIT.md fully.
2. Skim services/web/{index.html,app.js,styles.css} and ROUTES.
3. Implement in the order above.
4. Stop when Acceptance Checklist passes.
5. Do not expand scope.
```

---

## Reference URLs（验证用）

- App: `http://<host>/data/#/overview`  
- API health: `http://<host>/data/api/health`  
- Agent health: `http://<host>/data/agent/health`  
- Grafana: `http://<host>/grafana/`  

本地预览：

```bash
# 若缺 vendor
# 在仓库根执行安装脚本中的 vendor 下载部分，或手动放入 services/web/vendor/

cd services/web
python -m http.server 8088
# 打开 http://127.0.0.1:8088/#/overview
# 若需连远程 API，临时改 meta api-base 为 http://<host>/data/api（勿提交该临时改动）
```
