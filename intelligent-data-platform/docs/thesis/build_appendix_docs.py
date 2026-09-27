"""按学校模板填充《任务书》与《开题报告》。

设计原则（与论文导出脚本一致）：
  * **不重画表格**：直接打开学校模板，往它原有的合并单元格里写内容 ——
    这样版式、合并关系、页边距与我们看到的模板完全一致。
  * 填写说明（【填写说明】等内容）在打印时本应删除，脚本里直接换成正式内容。
  * 指导教师意见 / 签字处按模板要求**必须手写**，因此保留空白。
  * 个人信息来自 md_to_thesis_docx.COVER_FIELDS，避免两处各写一份而漂移。
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from md_to_thesis_docx import COVER_FIELDS, FONT_BODY_CN, FONT_BODY_EN  # noqa: E402

TEMPLATE_DIR = SCRIPT_DIR / "校内模板"
# 输出直接落到**交付目录**，与 01_毕业论文正文.docx 并列，
# 避免在 docs/thesis/ 根下再留一份会被误当作成稿的旧副本。
OUT_DIR = SCRIPT_DIR / "毕业论文材料_蒋树阳"
FIVE = 10.5   # 五号


def _fmt(run, size: float = FIVE, bold: bool = False) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = FONT_BODY_EN
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = rPr.makeelement(qn("w:rFonts"), {})
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), FONT_BODY_EN)
    rFonts.set(qn("w:hAnsi"), FONT_BODY_EN)
    rFonts.set(qn("w:eastAsia"), FONT_BODY_CN)


def set_cell(cell, text: str, *, size: float = FIVE, align_left: bool = True) -> None:
    """写入单元格：清空原有段落，按行分隔写多段。"""
    cell.text = ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        para = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT if align_left else WD_ALIGN_PARAGRAPH.CENTER
        pf = para.paragraph_format
        pf.line_spacing = 1.0          # 模板要求"单倍行距"
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        _fmt(para.add_run(line), size=size)


def set_title_year(doc, text: str) -> None:
    """把表题里的「20    届」补成「2027 届」。"""
    for p in doc.paragraphs:
        if "届" in p.text and "毕业论文" in p.text:
            new = p.text.replace("20    届", f"{COVER_FIELDS['grade']} 届") \
                        .replace("20   届", f"{COVER_FIELDS['grade']} 届")
            for r in list(p.runs):
                r.text = ""
            if p.runs:
                p.runs[0].text = new
            else:
                _fmt(p.add_run(new), size=16, bold=True)
            break


# ====================================================================== 任务书
TASK_CONTENT = """（1）实时链路：Kafka 统一事件入口 → Flink 1 分钟滚动窗口聚合 → Doris ADS 层，产出秒级指标；
（2）离线链路：MySQL 与 Kafka 经 Spark 抽取至 MinIO/S3A 上的 Parquet，按 ODS → DWD → DWS → ADS 四层建模，由 Hive Metastore 提供表目录；
（3）湖仓表格式迁移：将分层主表迁移为 Apache Iceberg v2 表（HiveCatalog），并逐表核对行数与金额合计；
（4）批流交叉对账：在两侧数据范围求交、尾部留安全边界的对账区间内，按 window_start 全外连接、缺失侧补 0，逐窗口比对交易域 7 个可加指标 + 1 个去重指标，以及流量域 uv / pv / 5 个行为计数共 7 个判据列；
（5）指标口径治理：以单一指标口径文件作为实时、离线与问答 Agent 的共同定义来源，并建立数据质量校验清单与监控看板；
（6）服务与智能层：只读数据服务（FastAPI）+ 数据看板（Vue / ECharts）+ 自然语言问答 Agent（LangGraph 编排、BM25 词法检索、MCP 工具暴露），Agent 不持有数据库凭据；
（7）测试与验证：单元测试与冒烟测试、各阶段验收脚本、性能基线采集，以及可复现的一键验收命令。"""

TASK_REQUIRE = """（1）技术栈版本必须明确钉死，禁止使用 latest 标签；镜像与依赖版本逐项记录，保证可复现；
（2）MySQL 为唯一事实源；ODS / DWD / DWS / ADS 四层命名与字段类型统一，金额一律 DECIMAL(18,2)，禁止用浮点累加金额；
（3）实时与离线必须共用同一份指标口径定义，不得各自另立第二份；
（4）数据访问必须使用专用只读账号，禁止复用 root；所有 SQL 经安全守卫检查，仅允许 SELECT、强制 LIMIT、拒绝白名单外的表；
（5）Agent 不得持有数据库凭据，只能经 HTTP 调用只读数据服务；回答必须给出所用表与实际执行的 SQL；
（6）全部数据为程序生成的合成数据，须带随机种子以保证可复现；业务约束（引用完整性、金额关系、时间因果、漏斗收窄）在生成期强制保证；
（7）单机资源受限（4 核 / 16 GB），离线批处理必须经受内存闸门，采用错峰执行（暂停 Flink 栈 → 跑批 → 强制恢复并自检）；
（8）每个阶段都必须走完"理解 → 规划 → 小步实现 → 测试 → 验证 → 文档 → 提交"，并提供一键验收脚本；
（9）论文与文档中的每个数字都必须可追溯到代码、SQL、测试或实测记录；无法取得实测依据的一律写"未采集"，不得估算。"""

TASK_REFS = """[1] KREPS J, NARKHEDE N, RAO J. Kafka: a distributed messaging system for log processing[C]//Proceedings of the NetDB Workshop. Athens: ACM, 2011: 1-7.
[2] CARBONE P, KATSIFODIMOS A, EWEN S, et al. Apache Flink: stream and batch processing in a single engine[J]. Bulletin of the IEEE Computer Society Technical Committee on Data Engineering, 2015, 38(4): 28-38.
[3] ZAHARIA M, CHOWDHURY M, DAS T, et al. Resilient distributed datasets: a fault-tolerant abstraction for in-memory cluster computing[C]//Proceedings of the 9th USENIX Conference on Networked Systems Design and Implementation. San Jose: USENIX Association, 2012: 15-28.
[4] ARMBRUST M, GHODSI A, XIN R, et al. Lakehouse: a new generation of open platforms that unify data warehousing and advanced analytics[C]//Proceedings of the 11th Conference on Innovative Data Systems Research. Chaminade: CIDR, 2021: 1-8.
[5] ROBERTSON S, ZARAGOZA H. The probabilistic relevance framework: BM25 and beyond[J]. Foundations and Trends in Information Retrieval, 2009, 3(4): 333-389.
[6] LEWIS P, PEREZ E, PIKTUS A, et al. Retrieval-augmented generation for knowledge-intensive NLP tasks[C]//Advances in Neural Information Processing Systems 33. Vancouver: Curran Associates, 2020: 9459-9474.
[7] YAO S, ZHAO J, YU D, et al. ReAct: synergizing reasoning and acting in language models[C]//Proceedings of the 11th International Conference on Learning Representations. Kigali: OpenReview, 2023: 1-19.
[8] Apache Software Foundation. Apache Iceberg table specification (version 2)[S/OL]. https://iceberg.apache.org/spec/, 2026-09-27.
[9] Anthropic. Model Context Protocol specification, revision 2026-07-28[S/OL]. https://modelcontextprotocol.io/specification, 2026-09-27.
[10] Apache Software Foundation. Apache Flink documentation: event time and watermarks[EB/OL]. https://nightlies.apache.org/flink/flink-docs-release-1.20/, 2026-09-27."""

TASK_SCHEDULE = [
    "2027 年 1 月 6 日\n—— 1 月 20 日",
    "2027 年 1 月 21 日\n—— 2 月 10 日",
    "2027 年 2 月 11 日\n—— 3 月 10 日",
    "2027 年 3 月 11 日\n—— 4 月 20 日",
]

TASK_MILESTONE = [
    "选题与需求分析：确定题目与技术路线，完成开题报告。",
    "环境与实时链路：容器编排、数据生成器、Kafka → Flink → Doris 打通。",
    "离线链路与对账：分层建模、Iceberg 迁移、批流逐窗口对账。",
    "服务与智能层、测试收口：数据服务、看板、Agent、质量校验与监控、性能基线。",
]


def build_task_book() -> Path:
    doc = Document(str(TEMPLATE_DIR / "毕业论文(设计)任务书.docx"))
    set_title_year(doc, "")
    t = doc.tables[0]
    f = COVER_FIELDS
    # r0/r1：姓名 / 学院 / 学号 / 专业班级（含纵向合并，写一次即可）
    set_cell(t.cell(0, 1), f["name"], align_left=False)
    set_cell(t.cell(0, 4), f["college"], align_left=False)
    set_cell(t.cell(0, 8), f["sid"], align_left=False)
    set_cell(t.cell(1, 8), f["cls"], align_left=False)
    set_cell(t.cell(2, 1), "基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台")
    set_cell(t.cell(3, 1), TASK_CONTENT)
    set_cell(t.cell(4, 1), TASK_REQUIRE)
    set_cell(t.cell(5, 1), TASK_REFS)
    # r6 进度安排：4 个时间段
    slots = [(0, 1), (0, 3), (0, 5), (0, 7)]
    for idx, (_, col) in enumerate(slots):
        set_cell(t.cell(6, col), TASK_SCHEDULE[idx], align_left=False)
    # r7 应该完成的内容：4 个里程碑（对应 4 个时间段）
    for idx, col in enumerate([1, 3, 5, 7]):
        set_cell(t.cell(7, col), TASK_MILESTONE[idx])
    # r8 指导教师意见：留空待手写（模板明确要求手写）
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "02_附件1_任务书.docx"
    doc.save(str(out))
    return out


# ==================================================================== 开题报告
REVIEW = """一、研究背景

电商业务的数据同时以两种形态产生。一类是点击、加购、下单、支付、退款等持续不断的事件流，业务对时效性的要求是秒级；另一类是日级、月级的经营分析，对准确性与可回溯性的要求高于时效性。传统做法把两者建成两套独立系统：实时侧由流处理引擎产出看板指标，离线侧由批处理引擎产出报表。代价是同一个指标存在两份实现，口径容易分叉；一旦实时与离线对同一个"GMV"给出不同数值，建立在其上的经营判断与智能应用就失去了依据。

这一问题的本质不是"引擎选型"，而是**指标口径的单一事实源缺失**。流处理与批处理在事件时间语义、去重近似算法、除法精度与缺失值约定上的差异，都会让"同一个数"变成两个数。要证明两条链路算的是同一个数，仅靠"两条链路都跑通了"是不够的，必须给出可证伪的逐项比对证据。

二、国内外研究状况

（1）流批一体的引擎路线。Flink 提出以统一的运行时同时承载流与批，用事件时间与水位线处理乱序数据，把批视为有界流；Spark 则以弹性分布式数据集（RDD）与结构化 API 为批处理基础，并用微批方式扩展流处理。两条路线都在向"一套代码、两种执行"演进，但**都没有解决"口径由谁定义"这一治理问题**——引擎一致不等于指标定义一致。

（2）湖仓一体与开放表格式。Lakehouse 主张在低成本对象存储上以开放表格式（如 Apache Iceberg）同时提供数仓的事务能力与数据湖的灵活性，用元数据层实现快照隔离、模式演进与时间旅行。Iceberg 的 v2 规范引入了行级删除与更完善的快照语义，使其可以承担"可回溯的事实层"。但湖仓解决的是**存储与事务**问题，逐窗口对账这类**跨链路一致性验证**仍需自行设计判据。

（3）数据质量与可观测性。业界普遍采用"校验即配置"的方式管理数据质量：每条校验是一段返回标量的 SQL 加一个判定算子，由统一引擎解释执行，失败时以非零退出码结束以便被调度系统拦截。这一形态的价值在于可枚举、可审计、可复现，而不是把校验写成散落的脚本分支。

（4）大模型与数据问答。ReAct 提出"推理—行动"交替的范式，让模型在调用工具后根据真实返回修正计划；检索增强生成（RAG）通过先检索可信语料再生成，缓解模型在缺乏依据时编造内容的问题。BM25 作为经典的词法排序函数，在语料规模有限、且回答必须给出可核对来源的场景下，仍具竞争力。Model Context Protocol（MCP）则把工具能力以标准协议暴露，使"同一份只读能力"能被不同客户端复用。这一方向的**共同风险是"看起来对"**：模型可能在查询失败后仍给出具体数字，因此必须由工程手段而非提示词来保证"失败即失败"。

三、发展趋势

综合上述，可以看到三条汇流：其一，实时与离线的边界在引擎层面逐渐消失，但在**指标定义与验证**层面反而更需要显式治理；其二，湖仓开放表格式正在成为事实层的默认选择，使数据可回溯、可迁移；其三，大模型进入数据栈的入口不是"替代 SQL"，而是**编排与解释**——把自然语言翻译为受约束的只读查询，并把证据（用了哪些表、执行了什么 SQL）一并呈现给使用者。

四、对本人研究的启发

上述工作给本设计确定了四条边界：

第一，**先工程、再智能**。必须先让数据可靠、准确、可查询、可治理，再让 Agent 使用数据；反过来做（先做聊天机器人再找数据）会把口径问题埋进模型回答里，且难以定位。

第二，**一致性主张必须是可证伪的**。受湖仓"开放表格式 + 快照"与流处理"事件时间窗口"的启发，本设计选择"按 window_start 全外连接、缺失侧补 0、逐窗口比对判据列"的对账方式，并明确声明其覆盖范围（对账区间内、计数类判据）与不覆盖之处（区间外、派生比率列）。

第三，**口径必须单点定义**。实时链路、离线链路与问答 Agent 共用同一份指标口径文件，且以它作为唯一权威；这直接来自"两份实现必然分叉"的教训。

第四，**工具能力要最小且可审计**。受 MCP 与 ReAct 的启发，问答 Agent 不持有数据库凭据，只能经 HTTP 调用只读数据服务；SQL 必须经安全守卫（仅 SELECT、表白名单、强制 LIMIT），回答必须附带实际执行的 SQL 与所用表。

附：参考文献目录见下方"研究思路与研究方法"之后所列（与论文正文参考文献一致，共 15 条）。"""

THINKING = """一、研究思路

以"两条独立链路算同一个数"为主线，按"数据可靠 → 数据准确 → 数据可查询 → 数据可治理 → Agent 使用数据"的顺序推进，每一步都留下可复核的证据：

（1）先建可复现的数据底座：程序生成合成数据，固定随机种子，并在生成期强制业务约束（引用完整性、金额关系、时间因果、漏斗逐级收窄），使"数据是否正确"本身不成为变量；
（2）再建两条独立链路，并让它们共用同一份指标口径定义；
（3）用逐窗口交叉对账把"两条链路一致"变成可证伪的断言，并显式披露判据范围与静默窗口；
（4）在一致的数据之上再叠加治理（质量校验 + 监控）与服务（只读数据服务 + 看板）；
（5）最后让 Agent 使用数据，并用进程边界（Agent 不持有凭据）与 SQL 守卫保证安全边界。

二、研究方法

（1）**实测驱动**：所有结论以实际运行结果为准；对版本行为差异、内存约束、超时与重试等问题，一律以复现实验定位，不凭经验判断。凡无法取得实测依据的内容，写作"未采集"而不估算。

（2）**对照实验**：对关键判断构造反证，例如"故意让一条校验失败以证明退出码非 0"、"故意把批次下界设为未来时刻以证明取不到本次批次时会失败而不是退回历史批次"。

（3）**判据先验证**：判据本身也需要被检验。本设计中的对账判据经过两次打磨（分区列导致的假失败；比率列从"两侧相等"改为"每侧按自己计数重算"），方向都是变严。

（4）**范围与结论一起写**：任何一致性结论都必须连同其覆盖范围、口径与已知缺陷一起表述。

（5）**一键可复现**：每个阶段提供验收脚本，使结论可被第三方重放。

四、写作提纲

1. 绪论（研究背景、意义、国内外研究现状、主要工作、组织结构）
2. 相关技术（版本钉法与各组件在本项目中的具体能力、否决备选与代价）
3. 需求分析（功能性、非功能性、约束与假设）
4. 系统设计（总体架构、部署分层、数仓分层与命名、存储选型、指标口径的唯一权威、安全设计、内存预算与错峰模式）
5. 关键实现（实时链路、离线分层建模、批流交叉对账、Iceberg 迁移、调度、服务层、Agent 图、检索、MCP、数据质量与监控、一次真实事故的完整复盘）
6. 测试与验证（测试体系与全量结果、逐窗口对账方法、结论与实测值、验收矩阵、性能基线）
7. 总结与展望（工作总结、主要贡献、不足、展望）
参考文献 / 致谢 / 附录"""

OUTLINE = """1. 绪论
   1.1 研究背景　1.2 研究意义　1.3 国内外研究现状　1.4 本文主要工作　1.5 论文组织结构
2. 相关技术
   2.1 技术栈与版本　2.2 实时计算：Kafka + Flink + Doris　2.3 湖仓与表格式：Spark + Iceberg
   2.4 调度：Airflow　2.5 大模型应用：Tool Calling、LangGraph、词法检索与 MCP　2.6 治理：数据质量与可观测性
3. 需求分析
   3.1 功能性需求　3.2 非功能性需求　3.3 约束与假设
4. 系统设计
   4.1 总体架构　4.2 部署分层原则　4.3 数仓分层与命名规范　4.4 存储选型与关键取舍
   4.5 指标口径的唯一权威　4.6 安全设计　4.7 内存预算与错峰模式
5. 关键实现
   5.1 实时链路　5.2 离线分层建模　5.3 批流交叉对账　5.4 Iceberg 迁移　5.5 调度：Airflow
   5.6 服务层　5.7 Agent 图　5.8 检索：BM25 词法检索与显式同义词表　5.9 MCP　5.10 数据质量与监控
   5.11 一次真实事故的完整复盘
6. 测试与验证
   6.1 测试体系与全量测试结果　6.2 逐窗口对账（验证方法）　6.3 结论、判据、实测值与复现方式
   6.4 验收矩阵　6.5 性能基线　6.6 采集过程中发现的一处真实缺陷
7. 总结与展望
   7.1 工作总结　7.2 主要贡献　7.3 不足　7.4 展望
参考文献　致谢　附录（A 术语与缩略语 / B 关键实测数字台账 / C 已知限制汇总 / D 复现命令清单 / E 图表清单）"""


def build_proposal() -> Path:
    doc = Document(str(TEMPLATE_DIR / "毕业论文(设计)开题报告.docx"))
    set_title_year(doc, "")
    t = doc.tables[0]
    f = COVER_FIELDS
    set_cell(t.cell(0, 1), f["name"], align_left=False)
    set_cell(t.cell(0, 4), f["college"], align_left=False)
    set_cell(t.cell(0, 8), f["sid"], align_left=False)
    set_cell(t.cell(1, 8), f["cls"], align_left=False)
    set_cell(t.cell(2, 1), "基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台")
    set_cell(t.cell(3, 1), REVIEW)
    set_cell(t.cell(4, 1), THINKING)
    # r5 是「写作提纲」独立一格。
    set_cell(t.cell(5, 1), OUTLINE)
    # r6/r7 是「指导教师意见 + 签名/时间」：模板写明**必须手写**。
    # !! 必须清掉模板自带的【填写说明】!!
    #   那是给填表人看的提示，模板自己标了"本说明打印时候删除"。
    #   留着会被当成"已填写内容"，而它其实是**要求教师写什么**的清单
    #   （⑴选题的研究价值 ⑵文献综述是否符合要求 …），位置也不对。
    set_cell(t.cell(6, 1), "")
    set_cell(t.cell(7, 2), "")
    set_cell(t.cell(7, 6), "\n年      月      日")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "03_附件2_开题报告.docx"
    doc.save(str(out))
    return out


if __name__ == "__main__":
    p1 = build_task_book()
    p2 = build_proposal()
    print(f"OK: {p1.name}  ({p1.stat().st_size:,} B)")
    print(f"OK: {p2.name}  ({p2.stat().st_size:,} B)")
