# -*- coding: utf-8 -*-
"""按 2024 修订版规范重建《任务书》与《开题报告》。

做法：直接以规范文档为基底（保留其样式/字体/表格 XML），清空正文后
插入 表题 + 模板表格 + 填充内容，避免重建表格带来的格式漂移。

正文文案在同目录的 forms_content_2024.py，改文案不必改本文件。
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

# A4 宽 11906 twips（21cm），左右页边距 1418 / 1276 ⇒ 版心 9212 twips。
# 模板里开题报告表宽 9204，恰好放得下；换成 3.17/3.17 的页边距就只有 8312，
# 整张表会左右各溢出版心约 0.8cm。
TEXT_WIDTH_TWIPS = 11906 - 1418 - 1276

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


def normalize_table_geometry(tbl_el):
    """保留模板自身的表格几何（tblW 用 dxa、固定布局、居中），只清掉重复项。

    !! 曾经这里把表格改成「页宽 100% + 自动布局」，但模板 tblPr 里本来就有
    !! tblW(dxa 9204) 与 tblLayout(fixed)，append 之后同一元素出现两个
    !! tblW、两个 tblLayout。OOXML 不允许重复，Word 取后一个（autofit），
    !! 于是列宽按内容重算，整张表看起来和模板不一样。模板的固定列宽
    !! 恰好等于正文宽度（9213 twips >= 9204），原本就不需要改。
    """
    tblPr = tbl_el.find(qn("w:tblPr"))
    if tblPr is None:
        return
    for tag in ("w:tblW", "w:tblLayout", "w:jc"):
        found = tblPr.findall(qn(tag))
        for extra in found[1:]:
            tblPr.remove(extra)


def assert_table_geometry(tbl_el, label: str) -> None:
    """校验表格几何与模板一致：单一 tblW(dxa)、单一 tblLayout(fixed)、列宽合计相等。"""
    tblPr = tbl_el.find(qn("w:tblPr"))
    widths = tblPr.findall(qn("w:tblW"))
    layouts = tblPr.findall(qn("w:tblLayout"))
    assert len(widths) == 1, "%s: tblW 出现 %d 次（应恰好 1 次）" % (label, len(widths))
    assert widths[0].get(qn("w:type")) == "dxa", "%s: tblW 不是 dxa" % label
    assert len(layouts) == 1, "%s: tblLayout 出现 %d 次（应恰好 1 次）" % (label, len(layouts))
    assert layouts[0].get(qn("w:type")) == "fixed", "%s: tblLayout 不是 fixed" % label
    total = sum(int(g.get(qn("w:w"))) for g in tbl_el.find(qn("w:tblGrid")))
    assert total == int(widths[0].get(qn("w:w"))), (
        "%s: 列宽合计 %d != tblW %s" % (label, total, widths[0].get(qn("w:w"))))
    # 表宽必须基本落在版心内。版心 = A4 宽 − 左右页边距 = 11906 − 1418 − 1276。
    # 模板自身的任务书表格宽 9320，比版心多 108 twips（0.19cm），Word 居中后
    # 左右各溢出 0.09cm，这是模板原样，故允许 200 twips 的容差；超过就要报错。
    overflow = total - TEXT_WIDTH_TWIPS
    assert overflow <= 200, (
        "%s: 表宽 %d twips 超出模板可容纳宽度 %d twips（溢出 %d twips ≈ %.2fcm），"
        "页边距或列宽设置有问题" % (label, total, TEXT_WIDTH_TWIPS, overflow, overflow / 567.0))
    print("  %s 表格几何: tblW=%s dxa, tblLayout=fixed, 列宽合计=%d, 版心=%d（溢出 %+d twips）"
          % (label, widths[0].get(qn("w:w")), total, TEXT_WIDTH_TWIPS, overflow))


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

    # 页面：A4 纵向。页边距必须用模板里"表格所在那一页"的值
    # （sec5 = 上2.54 下2.54 左2.50 右2.25，版心 16.25cm = 9213 twips），
    # 才能容下 9204 twips 宽的表格。若用 sec0 的 3.17/3.17（版心 14.66cm），
    # 表格会左右各溢出版心约 0.8cm —— 这是"看起来和模板不一样"的直接原因。
    # 注意：effective_sectpr() 在规范文档里取到的是 sec0（3.17），不能直接用。
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

    try:
        d.save(out_path)
    except PermissionError:
        # Word 打开着目标文件时 Windows 会独占它：改写到同目录的 "_新" 文件，
        # 提醒用户关闭后替换，避免"导出失败"被误当成代码问题。
        alt = os.path.splitext(out_path)[0] + "_新.docx"
        d.save(alt)
        print("目标文件被占用（Word 可能正打开它），已改写到:", alt)
        return alt
    return out_path


# ---------------------------------------------------------------- 1. 正文内容
# 正文全部来自同目录的 forms_content_2024.py：文案与组装逻辑分开维护，
# 不再从旧稿 docx 搬运，也不再就地插入段落（旧做法会在二次运行时读到自己的输出）。
from forms_content_2024 import (  # noqa: E402
    LIT,
    METHOD,
    OUTLINE,
    TASK_LIT,
    TASK_REQ,
    TASK_WORK,
    VALUE,
)

cur_task = docx.Document(CUR_TASK)
old_task = cur_task.tables[0]
# 参考文献取自基线《任务书》的第 6 行（旧模板该行标题是「参考文献」），
# 12 条 GB/T 7714 已逐条核对格式与顺序；2024 版已把这一行改为「文献资料收集要求」，
# 所以参考文献改由《开题报告》的文献综述栏末尾承载。
TASK_REFS = para_texts(old_task.rows[5].cells[-1])

LIT_CELL = LIT + ["", "附：参考文献"] + TASK_REFS
PROP_VALUE = VALUE + OUTLINE
PROP_METHOD = METHOD


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

print("文献综述段数=%d 字数=%d" % (len(LIT), sum(len(re.sub(r"\s", "", t)) for t in LIT)))
print("研究方法段数=%d 写作提纲段数=%d" % (len(METHOD), len(OUTLINE)))
print("任务书 参考文献条数=%d 进度=%d/%d" % (len(TASK_REFS), len(TASK_DATES), len(TASK_PLAN)))
assert len(TASK_DATES) == 4, "任务书进度必须是 4 行，实际 %d" % len(TASK_DATES)
assert len(TASK_PLAN) == 4, "任务书进度必须是 4 行，实际 %d" % len(TASK_PLAN)

# ---------------------------------------------------------------- 2. 生成开题报告
spec = docx.Document(SPEC)
prop_title = spec.paragraphs[205]
assert "开题报告" in prop_title.text, prop_title.text
prop_tbl = copy.deepcopy(spec.tables[1]._tbl)
relax_heights(prop_tbl)
normalize_table_geometry(prop_tbl)
assert_table_geometry(prop_tbl, "开题报告")

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

print("written", rebuild("开题报告", prop_tbl, OUT_PROP))

# ---------------------------------------------------------------- 3. 生成任务书
task_title = spec.paragraphs[202]
assert "任务书" in task_title.text, task_title.text
task_tbl = copy.deepcopy(spec.tables[0]._tbl)
relax_heights(task_tbl)
normalize_table_geometry(task_tbl)
assert_table_geometry(task_tbl, "任务书")

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

print("written", rebuild("任务书", task_tbl, OUT_TASK))
