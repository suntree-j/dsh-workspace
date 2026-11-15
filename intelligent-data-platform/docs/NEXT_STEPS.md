# 交接包的剩余事项（下一次开工从这里接）

> 写于 2026-11-16 05:5x，分支 `opt/20261116`（27 个提交领先 origin/main）。
> 已完成的部分见 `docs/evidence/` 下六份证据与 `docs/thesis/README.md` 的交付状态。
> 下面只列**还没做**的，每项都给了入口，不用重新摸。

## 1. 看板其余入口的日期与口径标注

已做：总览页（`#/overview`）与离线对账页（`#/batch`）。
未做：`#/realtime`、`#/trade`、`#/traffic`、`#/category`、`#/orders`、`#/quality`、`#/metrics`。

要统一的三件事：

1. **每页显示数据时间范围**——只有 3 页还缺。已核实的现状：
   - 总览：`index.html:303` 有 `数据窗口 … ~ …`（模型就是这行）；
   - 实时链：`:1596` 的「最新窗口」KPI 卡已经有窗口时刻，并解释了"滞后不适用"，
     页面上还有「最新窗口取值」面板（`:1616`），**不必再加**；
   - 订单明细：`:697` 明确写了"日期为闭区间，按订单事件时间过滤"，过滤条件本身即时间语义，**不必再加**；
   - 质量页：`:1453` 显示「对账批次 …」，批次号带时间，**不必再加**；
   - 指标口径页：纯字典，无数据时间，**不适用**；
   - **缺的只有 3 页**：交易分析（`:413`）、流量分析（`:522`）、类目销售（`:602`）。
     这三页的 `panel__meta` 只写了"最近 N 个窗口累计 / 共 N 个窗口"，是**计数不是时刻**；
     要补的是 `series` 首末行的 `window_start`。

   **注意：没有共享的页头组件**。`index.html` 里 10 个页面各是一个独立
   `<div class="page">`（256 / 413 / 522 / 602 / 693 / 848 / 951 / 1209 / 1424 / 1582 行），
   页头标题与副标题都是各写一遍的静态标记，所以不存在"改一处、十页生效"的位置。
2. **环比徽标统一走 `buildDelta()`** —— ✅ 已核实不必再改。
   `app.js` 里 6 处 `delta:` 调用（1406 / 1450 / 1455 / 1460 / 1669 / 1674）全部经由
   `buildDelta()`，历史判定在函数内部（`app.js:478`，阈值 `HISTORICAL_LIMIT_MIN`），
   没有任何页面绕过它自算环比。
3. **口径说明入口**：`#/metrics` 是指标口径字典页，各页的「?」提示应指向它而不是各写一段。

入口：`services/web/index.html`（x-template）、`services/web/app.js`（页面组件与 `buildDelta`）。

## 2. 智能体 RAG 与 Skill 两节的论文回写

交接包第五节列了五项：意图与执行、RAG 业务知识、Skill 任务知识、MCP 取数、回答与决策支持。
已完成：意图与执行（能力边界 `services/agent/app/scope.py`）、MCP 取数取证（`docs/evidence/E05_智能体路径.md` 第六节）。

未做：RAG 与 Skill 的正文段落——需要写清"检索到什么、怎么用、错了会怎样"，
数据来源是 `services/agent/app/{corpus,retrieval,lexical}.py` 与 66 条检索库。

入口：`docs/thesis/REPORT_DRAFT.md` 的 `## 5.8 检索` 与 `## 5.9 MCP` 两节，
写完重跑 `python docs/thesis/md_to_thesis_docx.py`。

## 3. 推送（阻塞在凭据，不在代码）

`github.com:443` 被连接重置；`api.github.com` 与 `github.com:22` 可达。
两条路：把 `~/.ssh/id_ed25519.pub` 加到 GitHub 后 `git push github-ssh main`，或开代理走 origin。

## 4. 不要在本地跑的东西

`scripts/verify-sprint-*.sh`、`tests/` 里的 smoke 标记用例都需要真实服务。
测试怎么跑、为什么两套 `app` 包不能同批跑，见 `docs/TESTING.md`。
