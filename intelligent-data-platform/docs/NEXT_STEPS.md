# 交接包的剩余事项（下一次开工从这里接）

> 写于 2026-11-16 05:5x，分支 `opt/20261116`（当前 35 个提交领先 origin/main；本提交后为 36）。
> 2026-11-16 07:59 更新：D3 的 `kafka_count > 0` 归档单跑已补齐，见 `docs/evidence/D3_调度失败态与湖仓发布完整性.md` 第 3.6 节。
> 已完成的部分见 `docs/evidence/` 下六份证据与 `docs/thesis/README.md` 的交付状态。
> 下面列剩余事项，并保留已完成项的入口与结论，避免下次重复摸底。

## 1. 看板其余入口的日期与口径标注

已做：总览页（`#/overview`）与离线对账页（`#/batch`）。
已完成（2026-11-16）：交易、流量、类目三页已补数据窗口并部署截图，证据见 `docs/evidence/W2_流量与类目数据窗口.md`；其余入口按下面理由维持不加。

要统一的三件事：

1. **每页显示数据时间范围**——当时只有 3 页还缺。已核实的现状：
   - 总览：`index.html:303` 有 `数据窗口 … ~ …`（模型就是这行）；
   - 实时链：`:1596` 的「最新窗口」KPI 卡已经有窗口时刻，并解释了"滞后不适用"，
     页面上还有「最新窗口取值」面板（`:1616`），**不必再加**；
   - 订单明细：`:697` 明确写了"日期为闭区间，按订单事件时间过滤"，过滤条件本身即时间语义，**不必再加**；
   - 质量页：`:1453` 显示「对账批次 …」，批次号带时间，**不必再加**；
   - 指标口径页：纯字典，无数据时间，**不适用**；
   - **当时缺的只有 3 页**：交易分析（`:413`）、流量分析（`:522`）、类目销售（`:602`）。
     这三页的 `panel__meta` 只写了"最近 N 个窗口累计 / 共 N 个窗口"，是**计数不是时刻**；
     要补的是 `series` 首末行的 `window_start`。

   **流量页为什么炸（已修复；保留为踩坑记录）**：在 `#tpl-traffic` 里调用 `fmtTime(...)`
   ——哪怕参数写死成字面量——整页会空白（截图 47 KB，只剩页头与侧栏）。
   做过的对照实验：把交易页同样的表达式搬到流量页 → 空白；换回原样 → 正常（107 KB）；
   再把参数换成 "2017-12-03 23:59:00" 字面量 → 又是 47 KB 空白。
   结论：**TrafficPanel 的 setup 返回值里没有 `fmtTime`**（对比：TradePanel 在 `app.js:1736`、
   OrdersPanel 在 `:2280` 都导出了 `fmtTime: formatTime`）。
   修法（已实施）：在 `var TrafficPanel = { ... setup: function () { ... return { ... } } }` 的 return 里
   补一行 `fmtTime: formatTime,`，然后再用 `series[0].window_start`；
   改完必须截图确认，不能只看 diff。

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

已完成（2026-11-16）：5.8 与 5.9 已按实测口径回写。5.8 更新为 69 条语料（metric 22 / convention 16 / topic 4 / layering 8 / table 19，交接描述的 66 条与本次接口返回不一致，以 `/retrieval` 实测为准），补 `doc_id`/`citation`/`as_context`/无命中与降级语义，并写明标题加成未彻底消除“约定行压过定义行”；Skill 明示为设计边界、未实现。5.9 按“实际 HTTP / MCP 可选已部署 / 工具级对照”三分法改写，明确未做完整 MCP 问答链路。DOCX 已重建（937,342 字节，34 表 7 图，断言通过）。证据：`docs/evidence/W3_论文5.8_5.9回写.md`。

入口：`docs/thesis/REPORT_DRAFT.md` 的 `## 5.8 检索` 与 `## 5.9 MCP` 两节；
重建命令：`python docs/thesis/md_to_thesis_docx.py`。

## 3. 推送（阻塞在凭据，不在代码）

`github.com:443` 被连接重置；`api.github.com` 与 `github.com:22` 可达。
两条路：把 `~/.ssh/id_ed25519.pub` 加到 GitHub 后 `git push github-ssh main`，或开代理走 origin。

## 4. 不要在本地跑的东西

`scripts/verify-sprint-*.sh`、`tests/` 里的 smoke 标记用例都需要真实服务。
测试怎么跑、为什么两套 `app` 包不能同批跑，见 `docs/TESTING.md`。
