# Sprint 9 — RAG + Metadata（元数据/口径检索增强）

> 项目：基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台
> 依据：[`docs/PROJECT_DESIGN_V1.md`](../PROJECT_DESIGN_V1.md) 第 7 章（Agent 架构）、
> 第 8 章 Roadmap 第 9 行；[`docs/DECISIONS.md`](../DECISIONS.md) ⏳7（检索方案）
> 前置：Sprint 7（Agent 主体）、Sprint 8（LangGraph 图式编排）
> 状态：✅ **已完成并验收通过**（`verify-sprint-9.sh` 59/0/0；详见第 9 节「实施记录」）

---

## 1. 目标

`AGENTS.md` §10.1 第 3、4 条要求「Agent 必须知道指标定义」「Agent 必须知道表结构」。
Sprint 7 的做法是**全量注入提示词**：表清单与口径要点直接写死在 `SYSTEM_PROMPT` 里。
它在当前体量下能工作，但有两个明确的上限：

| 上限 | 说明 |
| --- | --- |
| 体量上限 | 提示词已含约 2KB 的表清单；表数或口径条目再涨，提示词会挤占上下文与成本 |
| **可追溯上限** | 提示词里"提过一句"不等于"引用了权威口径"。回答给不出**命中的是哪份文档、哪一行** |

Sprint 9 要让 Agent **先查口径与表结构，再写 SQL**，并且把"命中的口径来源"作为
回答的一部分返回（与 `tables` / `executed_sql` 并列）：

```text
问题 → retrieve(query) → 命中的口径/表结构/分层文档（含来源文件与行号）→ 规划 → 取数 → …
```

---

## 2. 检索方案：**词法检索优先，不引入向量库**（依据 DECISIONS ⏳7）

### 2.1 为什么不是向量检索（事实与依据）

| 事实 | 依据 |
| --- | --- |
| DeepSeek 官方 API **没有 embeddings 端点** | 已查证：官方文档只提供对话补全；社区 issue 与 `/v1/embeddings` 404 报错为证 |
| 本机**放不下**本地 embedding 模型 | 4 核 / 16 GB，实时链路常驻约 13.7 GB，可用内存约 4.3 GB；embedding 模型 + 推理运行时放不下 |
| 向量库属**未批准技术栈** | `AGENTS.md` §2.3 明确禁止未经批准引入 `Milvus` / `Elasticsearch` |

若坚持向量检索，就必须**再引入一个外部供应商与 Key**（新增外部依赖），
或引入一个跑不动的本地模型。两条路都更差。

### 2.2 为什么词法检索在本项目**够用**（不是妥协）

1. 语料是**几十篇**量级（口径指标行 + 表结构 + 事件格式 + 分层说明），BM25 的排序质量足够；
2. 提问高度依赖**关键词**：`GMV`、`UV`、`复购率`、`ads_batch_trade_1m`、`payment_success_rate` ——
   这些正是 BM25 最擅长的精确词项匹配；
3. **命中理由可解释**：可以说清"因为命中了 `gmv` 与同义词扩展 `卖了多少钱→GMV`"，
   而向量相似度给不出这种理由 —— 而本项目的回答**本来就要给来源**；
4. **零新增基础设施、零新增依赖**（与"不许新增技术栈"直接一致）。

### 2.3 明确的代价与对策

| 代价 | 对策 |
| --- | --- |
| 语义相近但**用词不同**的提问召回不到（"卖了多少钱" vs "GMV"） | **显式同义词表**（`synonyms.json`）。它是一份**配置**，不是藏在代码里的 if-else：可审查、可增删、可测试 |
| 中文没有空格，BM25 的"词"边界不明 | 自实现分词：ASCII 词按词边界切分；**CJK 连续串切 bigram**（中文 IR 的标准做法，如"复购率"→`复购`/`购率`）。不引入 `jieba`（新增依赖） |
| 词法后端将来可能不够用 | **先抽象接口**：`Retriever.retrieve(query) -> docs`。日后换向量后端只替换这一个实现类，Agent 与图不动 |

### 2.4 语料（corpus）

| 来源 | 内容 | 取法 |
| --- | --- | --- |
| `sql/metadata/metrics.md` | 指标口径（唯一权威）、空值约定、可加/去重/派生分类 | **随代码分发**，进程内直接读文件（仓库内路径，非新增依赖、非数据库） |
| `sql/metadata/kafka_topics.md` | Kafka 事件格式与事件类型 | 同上 |
| 表结构说明 | 表名、字段、类型、注释、所属库 | 经既有只读接口 `GET /meta/tables` 取（**HTTP，不是数据库**），进程内按 TTL 缓存 |
| 分层说明 | ODS/DWD/DWS/ADS 与两条链路的粒度差异 | 随代码分发的静态文档（`services/agent/knowledge/layering.md`） |

> **架构边界澄清（重要）**：Agent 读自己仓库里的文档**不属于持有数据库凭据**。
> `AGENTS.md` §10.1 第 1 条约束的是"数据库管理员权限"；RAG 语料是**知识**，
> 不是**凭据**。Agent 仍然没有任何数据库连接信息，取数仍只经 `POST /query`。
> 这条边界写在这里，是为了避免后来者把"读文档"与"读数据库"混为一谈。

---

## 3. 架构

```text
services/agent/
  knowledge/
    synonyms.json          ← 同义词表（显式配置：短语 → 术语，附理由）
    layering.md            ← 数仓分层与两条链路粒度说明（静态语料）
  app/
    corpus.py              ← 语料装载与分块（Doc: doc_id/source_file/line/title/text）
    lexical.py             ← BM25 词法检索后端 + 同义词扩展 + 分词
    retrieval.py           ← 对外接口 retrieve(query, k) -> RetrievalResult（可换后端）
```

### 3.1 文档模型

```python
@dataclass(frozen=True)
class Doc:
    doc_id: str          # 稳定标识，如 metrics.md#L33
    title: str           # 人类可读标题，如 "GMV"
    text: str            # 可检索正文（已含字段名/口径/计算方式）
    source: str          # 来源文件，如 sql/metadata/metrics.md
    line: int            # 起始行号（可点回原文）
    kind: str            # metric | table | convention | topic | layering
```

**为什么要行号**：回答里写"依据 `sql/metadata/metrics.md`"只能证明"有这么个文件"；
写上 `L33` 才能让人**当场翻到那一行核对**。可核对性是本项目对"来源可追溯"的一贯要求。

### 3.2 BM25 与同义词扩展

```text
query
  ├── 分词（ASCII 词 + CJK bigram）
  ├── 同义词扩展（短语优先：长短语命中先记录，再扩展术语）
  │      "卖了多少钱" → GMV(weight 1.0)
  │      "复购"       → 复购率
  └── BM25 打分（k1=1.2, b=0.75），术语加权，短语加成
        ↓
      并列：命中的词项（用于解释"为什么是它"）
```

**同义词表必须能被验证生效**：验收项包含
「一个**不含 `GMV` 字样**的问法（如"最近一周卖了多少钱"）能命中 GMV 口径文档」，
并在响应里能看到 `expanded_terms` 里有 `卖了多少钱→GMV`。

### 3.3 与图（Sprint 8）的接线

`retrieve` 是图的**第一个节点**，不是"让模型自己决定要不要查"：

```text
retrieve（确定性执行，命中写入 state.docs）
   ↓
plan（提示词里带上 state.docs 的格式化结果）
   ↓
execute → validate → reflect → …
```

**为什么不让模型决定要不要检索**：检索是**每问必需**的前置步骤，
让模型"有时想起来查"会得到不稳定的口径引用 —— 而这正是本 Sprint 要消除的问题。
同时 `retrieve` 仍作为第 5 个工具注册进 `/tools`（能力面可枚举、可审查），
但**不参与模型的工具选择循环**。

---

## 4. 阶段划分

| 阶段 | 内容 | 完成判据 |
| --- | --- | --- |
| 1 | 写本任务书 | 本文件存在 |
| 2 | 语料层 `corpus.py` + `knowledge/` | `load_corpus()` 返回非空文档集，doc 数 > 50 |
| 3 | 词法层 `lexical.py` + 同义词表 | `pytest tests/test_retrieval.py -q` 全绿 |
| 4 | 接口层 `retrieval.py` + `GET /api/retrieve` | 接口返回 `hits` / `expanded_terms` / `sources` |
| 5 | 接入图（`retrieve` 节点） | 回答里出现 `docs` 且带 `source`/`line` |
| 6 | `scripts/verify-sprint-9.sh` | 分步 `[OK]/[FAIL]`、统计、失败 exit 1 |
| 7 | 端到端实证（含同义词生效） | 见第 5 节 DoD |
| 8 | 回归 Sprint 7 / 8 | `verify-sprint-7.sh` 49/49；`verify-sprint-8.sh` 通过 |
| 9 | 文档 | 本文件第 9 节 + `DEVELOPMENT_LOG.md` + `README.md` |

---

## 5. Definition of Done

| # | 判据 | 判定方式（必须有实证） |
| --- | --- | --- |
| 1 | 语料真实装载 | `/api/retrieve?q=gmv` 返回 `docs_total > 0`，且来源含 `sql/metadata/metrics.md` |
| 2 | 检索可解释 | 每条命中含 `source` / `line` / `score` / `matched_terms` |
| 3 | **同义词真实生效** | 问「最近一周卖了多少钱」→ 命中标题为 `GMV` 的文档，且 `expanded_terms` 含 `卖了多少钱→GMV` |
| 4 | 口径类问答引用到文档 | 问「复购率怎么算」→ 回答含命中的口径来源（若语料中确无该口径，必须**如实说明语料中没有**，不得编造公式） |
| 5 | 回答带三类来源 | 响应同时含 `tables` / `executed_sql` / `docs` |
| 6 | 未引入向量库 | `pip list` 中无 `milvus` / `faiss` / `elasticsearch`；`requirements.txt` 无相关项 |
| 7 | 离线可测 | `pytest -m unit` 全绿（检索为纯函数，不依赖服务） |
| 8 | 回归 | `verify-sprint-7.sh` 49/49、`verify-sprint-8.sh` 通过 |

---

## 6. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 同义词表变成"什么都映射"的垃圾桶 | 检索退化成"总能命中点什么"，反而误导模型 | 每条映射必须给出**理由**；单元测试包含**负例**（不该命中的不能命中） |
| BM25 在少数文档上 idf 退化 | 排序不稳 | 语料分块到条目级（数十至上百条），并在测试里锁定"口径问题命中口径文档"这一行为 |
| `/meta/tables` 不可达 | 语料缺表结构 | 缓存 + 降级：不可达时用静态分层文档兜底，并在 `/api/retrieve` 的 `channels` 里如实标注 `live_tables=false` |
| 分块把口径表切坏 | 命中文本残缺，模型据此写错 SQL | 分块规则按 markdown 表格**逐行**成块，单元测试断言"GMV 块含 `SUM(amount)` 与 `ORDER_CREATED`" |
| 声称"RAG"却只是关键词 `grep` | 论文答辩被质疑 | 文档写清：这是 **BM25 词法检索**，含 idf 与长度归一化；**不声称**语义检索。诚实描述比夸大更安全 |

---

## 7. 配置项（新增）

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_RETRIEVAL_TOP_K` | `5` | 单次检索返回的文档条数 |
| `AGENT_RETRIEVAL_ENABLED` | `true` | 置 `false` 即关闭检索节点（排障用） |

---

## 8. 边界（本 Sprint 明确不做）

- **不做向量检索 / 不引入向量库**（第 2 节）；
- **不做重排模型（reranker）**：无本地模型可用，词法分数即最终分数；
- **不索引 `infrastructure/` 下的 SQL 作业源码**：语料只放"人写给人的口径与结构说明"，
  把作业源码当知识会让 Agent 引用实现细节而不是口径；
- **不做用户反馈闭环**（query log → 调权重）：属可观测性范畴（Sprint 11）。

---

## 9. 实施记录

### 9.1 落地的文件

| 文件 | 变更 |
| --- | --- |
| `services/agent/app/corpus.py` | **新增**：语料装载与分块（`Doc` / `Corpus` / markdown 表格逐行成块） |
| `services/agent/app/lexical.py` | **新增**：BM25 后端 + 同义词扩展 + 中文分词（纯标准库） |
| `services/agent/app/retrieval.py` | **新增**：`Retriever.retrieve(query)` 门面（语料缓存 + 可换后端） |
| `services/agent/knowledge/synonyms.json` | **新增**：同义词表（**55 条**短语 —— 以文件内 `synonyms` 字段实测为准，每条带理由） |
| `services/agent/knowledge/layering.md` | **新增**：分层/两条链路/对账/粒度陷阱的静态语料 |
| `services/agent/app/main.py` | 新增 `/api/retrieve`（检索可解释）、`/retrieval`（语料画像） |
| `services/agent/app/graph.py` | 图首节点 `retrieve`（确定性执行），检索结果进 `state.docs` 随回答返回 |
| `services/agent/app/tools.py` | 新增 `retrieve_docs` 工具声明 |
| `scripts/verify-sprint-9.sh` | **新增**：8 步验收 |
| `tests/test_retrieval.py` | **新增**：26 条单元测试（含负例） |

**未引入任何新依赖**：检索层只用标准库（`json` / `math` / `re` / `dataclasses`）。

### 9.2 语料实际构成（实测 `/retrieval`）

```text
docs_total = 65
  metric      22  ← sql/metadata/metrics.md 的指标行（逐行成块，同名合并）
  convention  15  ← 同文件的通用约定
  topic        4  ← sql/metadata/kafka_topics.md 的事件类型
  layering     8  ← services/agent/knowledge/layering.md
  table       16  ← GET /meta/tables（与 sqlguard 白名单逐表对应）

channels = {
  'sql/metadata/metrics.md'                  : 'ok（37 条）'   ← 文件读入
  'sql/metadata/kafka_topics.md'             : 'ok（4 条）'
  'services/agent/knowledge/layering.md'     : 'ok（8 条）'
  'live_tables'                              : 'ok（16 张表）' ← 经只读接口取
}
```

`channels` 是**刻意暴露**的：任何一路语料缺失都会在这里显示原因，
而不是让检索悄悄变差（"少了一路语料却假装完整"是最难发现的故障）。

### 9.3 同义词表实测生效（核心验收项）

```text
问：最近一周卖了多少钱        ← 问法里**不含** GMV 字样
  expanded : ('卖了多少钱', ['gmv', 'payment_amount'], w=1.00)
             ('卖了多少',   ['gmv'],                  w=0.90)
  hit      : metric:payment_amount | sql/metadata/metrics.md:38 | 支付金额（payment_amount）
  hit      : metric:gmv            | sql/metadata/metrics.md:33 | GMV（gmv）
```

**负例同样成立**（只有正例的话，"把所有词都映射一遍"的坏配置也能全绿）：

```text
问：今天天气怎么样适合钓鱼吗
  hits = []            ← 不硬凑命中
```

### 9.4 验收结果

```text
✅ bash scripts/verify-sprint-9.sh    通过 59   失败 0   跳过 0
✅ tests/test_retrieval.py            26 passed（纯单元测试，零外部依赖）
✅ 向量库检查                          已装依赖中 milvus/faiss/elasticsearch/chromadb/
                                      sentence-transformers 数量 = 0
```

### 9.5 踩坑记录（4 条，全部是"检索质量"类问题，只有真实问句能暴露）

#### 9.5.1 "提到某词的文档"压过"定义该词的文档"

问「最近一周卖了多少钱」（同义词 → `gmv` + `payment_amount`）时，
排第一的是 `metrics.md` 里那句**列举了所有可加指标**的约定行
（"可加指标 gmv / order_cnt / payment_amount / …"）——
它同时命中两个查询词项，长度惩罚压不住它；而真正定义 GMV 的那一行只命中一个词项。

**处理**：加"标题命中加成"（`TITLE_BONUS`）。理由是词袋模型没有字段概念，
**提到**某词的行不该压过**定义**该词的行。
另把同名指标（`gmv` 在交易域与类目域各一行）**合并成一条**，
既保证来源不重复，也避免"用户问 GMV 却拿到类目口径"。

#### 9.5.2 短词项是噪声：`payment_amount` 里的 `amount`

语料里的英文指标名自身含子串，ASCII 切分会同时产出 `payment_amount` 与 `amount`；
而 `amount` 出现在**每一行业务指标**里，于是把无关文档也拉进了结果。

**处理**：**给长词项当子串的短词项直接丢弃**（长词项已表达同一信息）。

#### 9.5.3 孤立中文单字是噪声：`怎么算` 里的 `算`

`算`、`的`、`是` 这类单字几乎出现在每一篇文档里，idf 很低却数量极多，
把 `matched_terms` 填满噪声 —— 表现为"看起来命中了很多，其实无关"。

**处理**：分词丢弃**孤立中文单字**（语料里真正有区分度的中文实体由 bigram 覆盖）。

#### 9.5.4 markdown 表头会被当成一条数据

第一版解析器把 `| 指标 | 字段名 |` 这行也当成了一条口径条目。
表头与数据行的**形态完全一样**，只有"它在分隔行 `| --- |` 的上一行"这个位置信息能识别它。

**处理**：解析器在遇到分隔行时回退掉紧邻的上一行。做成默认行为而不是让调用方各自判断 ——
调用方各自实现一遍必然有人漏掉（漏掉的后果是语料里多出一条叫"指标"的假条目）。

### 9.6 一处设计取舍的记录（不是缺陷，但值得写清楚）

`information_schema.tables` / `information_schema.columns` **在 `sqlguard` 的
授权表集合里**（`METADATA_TABLES`），因此 Agent 可以主动探测元数据。
实测问 4（"dwd_user_profile 有哪些字段"）它就是靠这个先确认"表不存在"再如实回答的 ——
**这让回答更可靠**，但也意味着 Agent 能做"库表结构探测"这类动作。
本 Sprint 未改动该授权集合（属数据服务侧边界），仅在此记录，供 Sprint 10（MCP 权限最小化）一并复核。

### 9.7 本 Sprint 明确没做的事（边界）

- 未做向量检索（第 2 节已论证）；未引入 reranker；
- 未索引 `infrastructure/` 下的作业源码（语料只放"人写给人的口径与结构说明"，
  把作业源码当知识会让 Agent 引用实现细节而不是口径）；
- 未做 query log 与反馈闭环（属 Sprint 11 可观测性）。

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| — | V1.0 | 建立 Sprint 9 任务书（实现前先行） |
| 2026-09-27 | V1.1 | 回填实施记录：语料 65 条 / 同义词 **55 条**（`synonyms.json` 的 `synonyms` 字段实测；V1.0~V1.1 曾写 54，属早期计数残留）；验收 59/0/0；4 条检索质量踩坑 |
