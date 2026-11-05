# -*- coding: utf-8 -*-
"""按 2024 修订版规范重建《任务书》与《开题报告》。

做法：直接以规范文档为基底（保留其样式/字体/表格 XML），清空正文后
插入 表题 + 模板表格 + 填充内容，避免重建表格带来的格式漂移。
"""
import copy
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.text.paragraph import Paragraph

SPEC = r"C:\Users\jsy28\Documents\xwechat_files\wxid_fxi9a10yivq222_3bb1\msg\file\2026-11\附件2：湖南工商大学本科生毕业论文管理规范汇总（2024年修订版）.docx"
_DELIV = r"C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform\docs\thesis\毕业论文材料_蒋树阳"
_BASE = r"C:\Users\jsy28\Desktop\dsh-workspace\intelligent-data-platform\.tmp\baseline"
# 读取基线快照，写出到交付目录（避免二次运行读到自己上一次的输出）
CUR_TASK = os.path.join(_BASE, "02_附件1_任务书.docx")
CUR_PROP = os.path.join(_BASE, "03_附件2_开题报告.docx")

OUT_TASK = os.path.join(_DELIV, "02_附件1_任务书.docx")
OUT_PROP = os.path.join(_DELIV, "03_附件2_开题报告.docx")

STUDENT = "蒋树阳"
COLLEGE = "前沿交叉学院"
SNO = "2325010007"
CLASSNAME = "大数据2303"
TOPIC = "基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台"
FIELD = "数据湖仓; 批流一体; 智能体"
TITLE_PREFIX = "湖南工商大学2027届毕业论文(设计)"

W = qn("w:")  # namespace helper prefix


# ---------------------------------------------------------------- helpers
def para_texts(cell):
    """取单元格内的段落文本列表（去掉首尾空行）。"""
    out = [p.text for p in cell.paragraphs]
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def make_run(text, size=10.5, bold=False):
    r = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:ascii"), "Times New Roman")
    rFonts.set(qn("w:hAnsi"), "Times New Roman")
    rFonts.set(qn("w:eastAsia"), "宋体")
    rPr.append(rFonts)
    if bold:
        rPr.append(OxmlElement("w:b"))
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "000000")
    rPr.append(color)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(int(size * 2)))
    rPr.append(sz)
    szcs = OxmlElement("w:szCs")
    szcs.set(qn("w:val"), str(int(size * 2)))
    rPr.append(szcs)
    r.append(rPr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r.append(t)
    return r


def make_para(text, indent=True, size=10.5, bold=False, align=None, before=0, after=0):
    p = OxmlElement("w:p")
    pPr = OxmlElement("w:pPr")
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:before"), str(int(before * 20)))
    spacing.set(qn("w:after"), str(int(after * 20)))
    spacing.set(qn("w:line"), "240")
    spacing.set(qn("w:lineRule"), "auto")
    pPr.append(spacing)
    if indent and text.strip():
        ind = OxmlElement("w:ind")
        ind.set(qn("w:firstLineChars"), "200")
        ind.set(qn("w:firstLine"), "420")
        pPr.append(ind)
    if align:
        jc = OxmlElement("w:jc")
        jc.set(qn("w:val"), align)
        pPr.append(jc)
    p.append(pPr)
    if text:
        p.append(make_run(text, size=size, bold=bold))
    return p


def smart_quotes(s):
    """把 ASCII 双引号按出现顺序转成中文弯引号（全文中英混排统一排版）。"""
    out = []
    opening = True
    for ch in s:
        if ch == '"':
            out.append("\u201c" if opening else "\u201d")
            opening = not opening
        else:
            out.append(ch)
    return "".join(out)


def fill_tc(tc, lines, indent=True, size=10.5, bold=False):
    """清空单元格并按行写入；行可以是字符串，也可以是 (text, dict) 元组。"""
    for p in tc.findall(qn("w:p")):
        tc.remove(p)
    for item in lines:
        if isinstance(item, tuple):
            text, opt = item
        else:
            text, opt = item, {}
        tc.append(make_para(
            smart_quotes(text),
            indent=opt.get("indent", indent),
            size=opt.get("size", size),
            bold=opt.get("bold", bold),
            align=opt.get("align"),
            before=opt.get("before", 0),
            after=opt.get("after", 0),
        ))


def row_tcs(tr):
    return tr.findall(qn("w:tc"))


def find_tr(tbl_el, index):
    return tbl_el.findall(qn("w:tr"))[index]


def relax_heights(tbl_el):
    """行高由 exact 改为 atLeast，避免长文本被裁掉。"""
    for tr in tbl_el.findall(qn("w:tr")):
        trPr = tr.find(qn("w:trPr"))
        if trPr is None:
            continue
        for h in trPr.findall(qn("w:trHeight")):
            h.set(qn("w:hRule"), "atLeast")


def set_table_pct(tbl_el):
    """表格整体宽度设为页宽的 100%，避免模板里错误的绝对宽度导致越界。"""
    tblPr = tbl_el.find(qn("w:tblPr"))
    if tblPr is None:
        return
    for w in tblPr.findall(qn("w:tblW")):
        tblPr.remove(w)
    tw = OxmlElement("w:tblW")
    tw.set(qn("w:type"), "pct")
    tw.set(qn("w:w"), "5000")
    tblPr.append(tw)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "autofit")
    tblPr.append(layout)


def effective_sectpr(body, anchor_el):
    """取 anchor_el 所在节的 sectPr。"""
    children = list(body.iterchildren())
    idx = children.index(anchor_el)
    for ch in children[idx:]:
        if ch.tag == qn("w:sectPr"):
            return ch
        if ch.tag == qn("w:p"):
            pPr = ch.find(qn("w:pPr"))
            if pPr is not None and pPr.find(qn("w:sectPr")) is not None:
                return pPr.find(qn("w:sectPr"))
    return body.find(qn("w:sectPr"))


def rebuild(key, tbl_el, out_path):
    """以规范文档为基底，生成只含 表题 + 表格 的单节文档。key 为「任务书」或「开题报告」。"""
    d = docx.Document(SPEC)
    body = d.element.body
    title_src = None
    for p in d.paragraphs:
        t = p.text.strip()
        if t.startswith("湖南工商大学") and key in t and "届" in t:
            title_src = copy.deepcopy(p._p)
            anchor = p._p
            break
    if title_src is None:
        raise RuntimeError("找不到表题段落: %s" % key)
    sect = copy.deepcopy(effective_sectpr(body, anchor))

    for ch in list(body.iterchildren()):
        body.remove(ch)

    # 页面：A4 纵向，页边距 2.54/2.54/2.50/2.25
    pgSz = sect.find(qn("w:pgSz"))
    if pgSz is None:
        pgSz = OxmlElement("w:pgSz")
        sect.append(pgSz)
    pgSz.set(qn("w:w"), "11906")
    pgSz.set(qn("w:h"), "16838")
    pgMar = sect.find(qn("w:pgMar"))
    if pgMar is None:
        pgMar = OxmlElement("w:pgMar")
        sect.append(pgMar)
    for k, v in (("top", "1440"), ("bottom", "1440"), ("left", "1418"), ("right", "1276"),
                 ("header", "851"), ("footer", "992"), ("gutter", "0")):
        pgMar.set(qn("w:" + k), v)
    for ref in sect.findall(qn("w:headerReference")) + sect.findall(qn("w:footerReference")):
        sect.remove(ref)

    # 清掉标题段落里的旧文字，写入新表题
    for r in title_src.findall(qn("w:r")):
        title_src.remove(r)
    for tag in ("w:hyperlink", "w:bookmarkStart", "w:bookmarkEnd"):
        for e in title_src.findall(qn(tag)):
            title_src.remove(e)
    title_src.append(make_run(TITLE_PREFIX + key, size=16, bold=True))

    body.append(title_src)
    body.append(tbl_el)
    body.append(sect)

    d.save(out_path)
    return out_path


# ---------------------------------------------------------------- 1. 读旧稿正文
cur = docx.Document(CUR_PROP)
old_table = cur.tables[0]
LIT = para_texts(old_table.rows[3].cells[-1])
# 2024 版《任务书》已把“参考文献”行改为“文献资料收集要求”，纠正旧稿的指向
LIT = [t.replace('"参考文献"栏', '"文献资料收集要求"栏') for t in LIT]
LIT = [t.replace("附：本课题参考文献", "本课题参考文献") for t in LIT]


def collapse_blank(seq):
    """把连续空行折叠为一个，避免插入内容造成的忽大忽小的段间距。"""
    out = []
    for x in seq:
        if not x.strip() and out and not out[-1].strip():
            continue
        out.append(x)
    return out
          # 文献综述
METHOD_ALL = para_texts(old_table.rows[4].cells[-1])   # 研究思路与研究方法
OUTLINE = para_texts(old_table.rows[5].cells[-1])      # 写作提纲


def find_idx(seq, prefix):
    return next(k for k, t in enumerate(seq) if t.strip().startswith(prefix))


def insert_block(seq, prefix, texts):
    """在某个标题前插入若干段（自动补空行分隔）。"""
    i = find_idx(seq, prefix)
    while i > 0 and not seq[i - 1].strip():
        i -= 1
    seq[i:i] = [""] + list(texts) + [""]


# 2024 版要求文献综述约 3000 字，原稿 2299 字，按三条主线各补一段（不新增文献）
insert_block(LIT, "二、国内外研究状况", [
    "从工程实践看，两个同义指标出现分叉的原因往往不是算法错误，而是口径细节：事件时间与处理时间的选择、迟到数据是否回填、去重是精确还是近似、除法分母为 0 时取 0 还是取空、金额用定点数还是浮点数。这些差异在单条链路上都无可指摘，合到同一个看板上就变成无法解释的差额。因此“两条链路是否一致”不能靠代码审查回答，只能靠对两侧同一时间窗口的计算结果做逐项比对来回答。",
    "",
    "更进一步，比对本身也有可信度问题。若对账只覆盖“两侧都有数据”的窗口，单边缺失就会被静默跳过；若把全部判据列都要求严格相等，比率列因浮点末位不同就会产生大量假失败。判据设计不当会同时制造假通过与假失败，而这两种错误都不会自己暴露出来。",
])

insert_block(LIT, "三、发展趋势", [
    "（5）跨引擎同一指标比对的现状。多数开源数据质量工具以单条链路内部的自洽为目标：规则写在链路自己的流程里，通过与否只反映“这一侧有没有脏数据”。跨引擎、跨链路的同一指标比对往往落在一份自建脚本里：区间起点如何取、尾部要不要留安全边界、单边缺失算不算失败、比率列要不要参与判定，这些决定通常只存在于实现细节中，缺少可复用的判据设计讨论。结果是“对账通过”在公开材料里很少附带它的覆盖范围与失效条件，第三方难以判断一条通过结论究竟验证了什么。",
])

insert_block(LIT, "四、对本人研究的启发", [
    "还有一条共同前提贯穿上述三条趋势：无论引擎、表格式还是模型如何演进，结论都必须能被第三方用同一份数据重放。可复现性因此从“工程好习惯”上升为“结论成立的必要条件”——一份无法重放的“一致”与一份无法重放的“高性能”，同样不可采信。",
])

LIT = collapse_blank(LIT)  # 必须在所有插入之后折叠，否则又会被插出双空行

# 把 r4 的“四、写作提纲”之后的内容切掉（提纲已并入选题价值）
cut = next(i for i, t in enumerate(METHOD_ALL) if t.strip().startswith("四、写作提纲"))
METHOD = METHOD_ALL[:cut]
while METHOD and not METHOD[-1].strip():
    METHOD.pop()

cur_task = docx.Document(CUR_TASK)
old_task = cur_task.tables[0]
TASK_WORK = para_texts(old_task.rows[3].cells[-1])
TASK_REQ = para_texts(old_task.rows[4].cells[-1])
TASK_REFS = para_texts(old_task.rows[5].cells[-1])
def _dedup(seq):
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out

TASK_DATES = _dedup([
    re.sub(r"\s+", "", "".join(x.strip() for x in para_texts(c))).replace("——", "—")
    for c in old_task.rows[6].cells[1:]
    if para_texts(c)
])
TASK_PLAN = _dedup([
    " ".join(x.strip() for x in para_texts(c))
    for c in old_task.rows[7].cells[1:]
    if para_texts(c)
])
TASK_PLAN = [t.replace("数据生成器", "真实数据接入与语义映射") for t in TASK_PLAN]

print("文献综述段数=%d 字数=%d" % (len(LIT), sum(len(re.sub(r"\s", "", t)) for t in LIT)))
print("研究方法段数=%d 写作提纲段数=%d" % (len(METHOD), len(OUTLINE)))
print("任务书 参考文献条数=%d 进度=%d/%d" % (len(TASK_REFS), len(TASK_DATES), len(TASK_PLAN)))

# ---------------------------------------------------------------- 2. 组装开题报告
VALUE = [
    "一、本课题弥补的空白",
    "（1）引擎层与存储层已有成熟答案，口径层没有。流批一体解决的是“一套运行时”，湖仓一体解决的是“一份存储”，但“两条链路算出的同一个指标是否相等”仍缺少可证伪的验证方法。本课题补的正是这一层：以唯一的口径文件作为单一事实源，用按窗口全外连接的对账把“一致”变成可复现的断言，而不是一句声称。",
    "（2）对账的判据本身也需要被检验。多数工程实践把“对账通过”当作结论，本课题把判据也当作对象：区分可加指标与去重指标，把比率列由“两侧相等”改为“每侧按自己的计数重算”，并引入尾部安全边界与静默窗口的显式声明，使一条“通过”的结论同时携带它的覆盖范围与失效条件。",
    "（3）合成数据上的对账容易“恰好相等”。本课题改用两个公开真实数据集，二者形态互不相同（一方有金额无行为流，一方有行为流无金额），且在用户、商品与时间上互不重叠。真实数据必然带来缺失用户标识、取消发票、负数量退货与整行重复，这些恰恰是口径分叉的真实来源；在它们之上仍能逐处归因，结论才有分量。",
    "",
    "二、论文结构与写作提纲",
    "全文共 7 章，另有参考文献、致谢与附录，结构如下（章下为节）：",
]
PROP_VALUE = VALUE + OUTLINE

LIT_CELL = LIT + ["", "附：参考文献"] + TASK_REFS

PROP_METHOD = METHOD

# ---------------------------------------------------------------- 3. 组装任务书补充内容
TASK_LIT = [
    "围绕流批一体计算、湖仓开放表格式、批流一致性校验与大模型数据问答四个方向收集文献，共 12 条：国外期刊与会议论文 7 篇（Kafka 分布式消息系统、Flink 流批统一引擎、Spark 弹性分布式数据集、Lakehouse 体系、BM25 相关度框架、检索增强生成、ReAct 推理与行动协同），技术规范与官方文档 3 份（Apache Iceberg v2 表规范、Model Context Protocol 规范、Apache Flink 事件时间与水位线文档），公开真实数据集 2 套（UCI Online Retail II、天池淘宝用户行为数据集）。",
    "检索以中国知网、IEEE Xplore、ACM Digital Library 及各项目官方网站为主，检索截至 2026 年 11 月。全部文献按 GB/T 7714—2015 著录，列于《开题报告》“文献综述”栏之后；其中两条为公开数据集的出处，用于支撑本设计的数据来源可查证。",
]

# ---------------------------------------------------------------- 4. 生成开题报告
spec = docx.Document(SPEC)
prop_title = spec.paragraphs[205]
assert "开题报告" in prop_title.text, prop_title.text
prop_tbl = copy.deepcopy(spec.tables[1]._tbl)
relax_heights(prop_tbl)
set_table_pct(prop_tbl)

trs = prop_tbl.findall(qn("w:tr"))
# r0: 姓名 / 学院 / 学号
fill_tc(row_tcs(trs[0])[1], [STUDENT], indent=False, )
fill_tc(row_tcs(trs[0])[3], [COLLEGE], indent=False)
fill_tc(row_tcs(trs[0])[5], [SNO], indent=False)
# r1: 专业班级
fill_tc(row_tcs(trs[1])[5], [CLASSNAME], indent=False)
# r2: 题目 / 研究方向
fill_tc(row_tcs(trs[2])[1], [TOPIC], indent=False)
fill_tc(row_tcs(trs[2])[3], [FIELD], indent=False)
# r3: 选题价值
fill_tc(row_tcs(trs[3])[1], PROP_VALUE)
# r4: 文献综述（含参考文献）
fill_tc(row_tcs(trs[4])[1], LIT_CELL)
# r5: 研究方法与思路
fill_tc(row_tcs(trs[5])[1], PROP_METHOD)
# r6: 指导教师意见 → 清空【填写说明】，留白待手写
fill_tc(row_tcs(trs[6])[1], [""])
# r7: 签名 / 时间 保持模板空白

rebuild("开题报告", prop_tbl, OUT_PROP)
print("written", OUT_PROP)

# ---------------------------------------------------------------- 5. 生成任务书
task_title = spec.paragraphs[202]
assert "任务书" in task_title.text, task_title.text
task_tbl = copy.deepcopy(spec.tables[0]._tbl)
relax_heights(task_tbl)
set_table_pct(task_tbl)

ttrs = task_tbl.findall(qn("w:tr"))
fill_tc(row_tcs(ttrs[0])[1], [STUDENT], indent=False)
fill_tc(row_tcs(ttrs[0])[3], [COLLEGE], indent=False)
fill_tc(row_tcs(ttrs[0])[5], [SNO], indent=False)
fill_tc(row_tcs(ttrs[1])[5], [CLASSNAME], indent=False)
fill_tc(row_tcs(ttrs[2])[1], [TOPIC], indent=False)
fill_tc(row_tcs(ttrs[2])[3], [FIELD], indent=False)
fill_tc(row_tcs(ttrs[3])[1], TASK_WORK)
fill_tc(row_tcs(ttrs[4])[1], TASK_REQ)
fill_tc(row_tcs(ttrs[5])[1], TASK_LIT)
for i in range(4):
    fill_tc(row_tcs(ttrs[7 + i])[0], [TASK_DATES[i]], indent=False, size=9)
    fill_tc(row_tcs(ttrs[7 + i])[1], [TASK_PLAN[i]], size=10.5)

rebuild("任务书", task_tbl, OUT_TASK)
print("written", OUT_TASK)
