# 交接包的剩余事项（下一次开工从这里接）

> 写于 2026-11-16 05:5x，分支 `opt/20261116`（27 个提交领先 origin/main）。
> 已完成的部分见 `docs/evidence/` 下六份证据与 `docs/thesis/README.md` 的交付状态。
> 下面只列**还没做**的，每项都给了入口，不用重新摸。

## 1. 看板其余入口的日期与口径标注

已做：总览页（`#/overview`）与离线对账页（`#/batch`）。
未做：`#/realtime`、`#/trade`、`#/traffic`、`#/category`、`#/orders`、`#/quality`、`#/metrics`。

要统一的三件事：

1. 每个页面顶部要能看出"这份数据的时间范围是什么"——用页面已有的 `数据窗口` 字段，
   不要用取数时刻（取数时刻是顶栏的「观测时间」）。
2. 历史数据集上不显示"比上一窗口涨跌"——`buildDelta()` 已按 `HISTORICAL_LIMIT_MIN` 处理，
   但只接在总览页；其余页面若有环比徽标，要确认都走这个函数。
3. 口径说明入口：`#/metrics` 是指标口径字典页，各页的「?」提示应指向它而不是各写一段。

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
