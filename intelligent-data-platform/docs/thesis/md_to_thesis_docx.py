#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 REPORT_DRAFT.md 转成可直接用 Word / WPS 打开的毕业论文 .docx。

本脚本只做**排版**，不改动报告正文内容；仅对 Markdown 解析做了必要的兼容处理
（详见 ``COMPAT_NOTES`` 与运行结束时的统计输出）。

约束（对齐技能 word-docx 的规则）：
  * 必须用 python-docx 生成，禁止手工拼 zip；
  * 生成后必须用 ``Document(path)`` 重新打开做冒烟检查。

用法：
    python md_to_thesis_docx.py
    python md_to_thesis_docx.py --input REPORT_DRAFT.md --output 毕业论文.docx
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Sequence

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Length, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph

LOG = logging.getLogger("md_to_thesis_docx")

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "REPORT_DRAFT.md"
DEFAULT_OUTPUT = SCRIPT_DIR / "毕业论文_基于Lakehouse与AIAgent的批流一体智能数据分析平台.docx"

# ---------------------------------------------------------------- 版式常量
FONT_BODY_CN = "宋体"
FONT_BODY_EN = "Times New Roman"
FONT_HEADING = "黑体"
FONT_MONO = "Consolas"

BODY_PT = 12.0          # 小四
H1_PT, H2_PT, H3_PT = 16.0, 14.0, 12.0   # 三号 / 四号 / 小四
TABLE_PT = 10.5         # 五号
CODE_PT = 9.0           # 代码块西文（9~10 pt）
CODE_CJK_PT = 10.0      # 代码块中文：Consolas 单元格宽 0.55 em，
                        # 10 pt 汉字（1 em）≈ 2 × 9 pt Consolas 单元格，ASCII 图才对得齐
CODE_LINE_PT = 14.0     # 代码块固定行距，避免中英混排把行高撑歪
FOOTER_PT = 10.5

PAGE_W_CM, PAGE_H_CM = 21.0, 29.7        # A4
# ---------------------------------------------------------------- 页边距
# !! 按《湖南工商大学本科生毕业论文（设计）撰写规范》㈣.9 / ㈥.4 的硬要求 !!
#   规范原文：「页面上边距：30mm；下边距：25mm；左边距：30mm；右边距：20mm。」
#   注意是**四边不对称**的：上=左=30mm，下=25mm，右=20mm。
#   原先用的 2.54 / 3.17 是 Word 默认值（上下 2.54、左右 3.17），
#   与学校要求不符 —— 形式审查会按规范核对页边距，所以必须改。
#   同时：正文行距为**固定值 22 磅**（规范㈥.4、㈦.4），不是 1.5 倍。
MARGIN_TOP_CM = 3.0      # 30mm
MARGIN_BOTTOM_CM = 2.5   # 25mm
MARGIN_LEFT_CM = 3.0     # 30mm
MARGIN_RIGHT_CM = 2.0    # 20mm
# 正文可用宽度 = 21 − 3.0 − 2.0 = 16.0 cm（表格列宽按它分配）
TEXT_WIDTH_CM = PAGE_W_CM - MARGIN_LEFT_CM - MARGIN_RIGHT_CM   # 16.0 cm

BODY_LINE_PT = 22.0      # 正文行距固定值 22 磅（规范要求）

CODE_FILL = "F5F5F5"
NOTE_FILL = "F7F7F7"
HEADER_FILL = "D9D9D9"
CODE_BORDER = "A6A6A6"
NOTE_BORDER = "8FAADC"

FIRST_LINE_CHARS = 200          # 首行缩进 2 字符
HANGING_CM = 0.74               # 悬挂缩进
CM_TO_PT = 72.0 / 2.54
QUOTE_LEFT_CM = 0.6

TOC_INSTR = r'TOC \o "1-3" \h \z \u'

COMPAT_NOTES: list[str] = []

# ------------------------------------------------- OOXML 元素顺序（schema）
_PPR_TAIL = (
    "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
    "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
    "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
    "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr",
    "w:sectPr", "w:pPrChange",
)
_TCPR_TAIL = (
    "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign",
    "w:hideMark", "w:tcPrChange",
)
_TRPR_TAIL = ("w:tblCellSpacing", "w:jc", "w:hidden")
_TBLPR_TAIL = (
    "w:jc", "w:tblCellSpacing", "w:tblInd", "w:tblBorders", "w:shd", "w:tblLayout",
    "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription", "w:tblPrChange",
)
_SECTPR_TAIL = (
    "w:cols", "w:formProt", "w:vAlign", "w:noEndnote", "w:titlePg", "w:textDirection",
    "w:bidi", "w:rtlGutter", "w:docGrid", "w:printerSettings", "w:sectPrChange",
)
_SETTINGS_BEFORE = (
    "w:hdrShapeDefaults", "w:footnotePr", "w:endnotePr", "w:compat", "w:docVars",
    "w:rsids", "w:mathPr", "w:themeFontLang", "w:clrSchemeMapping", "w:shapeDefaults",
    "w:decimalSymbol", "w:listSeparator",
)


# ===================================================================== 行内解析
@dataclass
class Token:
    """一段行内文本及其强调属性。"""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


def _find_code_spans(text: str) -> list[tuple[int, int]]:
    """返回 ``[(start, end), ...]``，end 为闭合反引号之后的下标。"""
    spans: list[tuple[int, int]] = []
    i = 0
    while True:
        a = text.find("`", i)
        if a == -1:
            break
        b = text.find("`", a + 1)
        if b == -1:
            break
        spans.append((a, b + 1))
        i = b + 1
    return spans


def parse_inline(text: str) -> list[Token]:
    """把一行 Markdown 行内文本切成 Token 序列（``**粗体**`` / ``*斜体*`` / ``` `代码` ```）。"""
    n = len(text)
    code_spans = _find_code_spans(text)
    in_code = bytearray(n)
    for a, b in code_spans:
        in_code[a:b] = b"\x01" * (b - a)

    # 1) 代码区之外的 ** 成对 → 粗体开关
    dbl = [
        i for i in range(n - 1)
        if text[i] == "*" and text[i + 1] == "*" and not in_code[i] and not in_code[i + 1]
    ]
    bold_marks: set[int] = set()
    if len(dbl) % 2 == 0 and dbl:
        bold_marks = set(dbl)
    elif dbl:
        LOG.warning("检测到不成对的 ** ，按字面量输出：%s", text[:60])

    # 2) 代码区之外、且不属于 ** 的单个 * 成对 → 斜体开关
    single = [
        i for i in range(n)
        if text[i] == "*" and not in_code[i]
        and not (i in bold_marks or (i - 1) in bold_marks)
    ]
    italic_marks: set[int] = set()
    if len(single) % 2 == 0:
        italic_marks = set(single)
    elif single:
        LOG.warning("检测到不成对的 * ，按字面量输出：%s", text[:60])

    code_start = {a: b for a, b in code_spans}
    tokens: list[Token] = []
    buf: list[str] = []
    bold = italic = False

    def flush() -> None:
        if buf:
            tokens.append(Token("".join(buf), bold, italic, False))
            buf.clear()

    i = 0
    while i < n:
        if i in code_start:
            end = code_start[i]
            flush()
            tokens.append(Token(text[i + 1:end - 1], bold, italic, True))
            i = end
            continue
        if i in bold_marks:
            flush()
            bold = not bold
            i += 2
            continue
        if i in italic_marks:
            flush()
            italic = not italic
            i += 1
            continue
        buf.append(text[i])
        i += 1
    flush()

    if bold or italic:      # 兜底：宁可保留原文，也不要吞掉字符
        LOG.warning("强调标记未闭合，按字面量输出：%s", text[:60])
        return [Token(text, code=False)]
    return tokens


def plain_text(text: str) -> str:
    """去掉行内标记，只留纯文本（用于标题等不需要强调的位置）。"""
    return "".join(tok.text for tok in parse_inline(text))


def _display_cells(text: str) -> int:
    """按中日韩全角 = 2 格估算显示宽度。"""
    width = 0
    for ch in plain_text(text):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


# ===================================================================== 块解析
@dataclass
class Block:
    """一个 Markdown 顶层块。"""

    kind: str                                  # heading/para/list/quote/quote_list/code/table/rule
    text: str = ""
    level: int = 0
    marker: str = ""
    lines: list[str] = field(default_factory=list)
    header: list[str] | None = None
    rows: list[list[str]] = field(default_factory=list)
    ncols: int = 0


_DELIM_CELL_RE = re.compile(r"^:?-{2,}:?$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^([-*+]|\d+[.)])\s+(.*)$")
_RULE_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")


def split_table_row(line: str) -> list[str]:
    """按 ``|`` 切分表格行；反引号里的 ``|`` 与 ``\\|`` 不算分隔符。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in "|`*_[]":
            buf.append(s[i + 1])
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
            i += 1
            continue
        if ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def parse_blocks(md: str) -> list[Block]:
    """把 Markdown 文本切成块序列。"""
    lines = md.split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        if s.startswith("```"):                       # 代码块
            j = i + 1
            body: list[str] = []
            while j < n and not lines[j].strip().startswith("```"):
                body.append(lines[j].rstrip())
                j += 1
            blocks.append(Block(kind="code", text=s[3:].strip(), lines=body))
            i = j + 1
            continue

        if _RULE_RE.match(s):                         # 分隔线
            blocks.append(Block(kind="rule"))
            i += 1
            continue

        m = _HEADING_RE.match(s)
        if m:
            blocks.append(Block(kind="heading", level=len(m.group(1)), text=m.group(2).strip()))
            i += 1
            continue

        if s.startswith("|"):                         # 表格
            raw_rows: list[list[str]] = []
            j = i
            while j < n and lines[j].strip().startswith("|"):
                raw_rows.append(split_table_row(lines[j]))
                j += 1
            blocks.append(_make_table(raw_rows))
            i = j
            continue

        if s.startswith(">"):                         # 引用块
            j = i
            while j < n and lines[j].strip().startswith(">"):
                inner = re.sub(r"^>\s?", "", lines[j].strip())
                if inner:
                    lm = _LIST_RE.match(inner)
                    if lm:
                        blocks.append(Block(kind="quote_list", marker=lm.group(1), text=lm.group(2)))
                    else:
                        blocks.append(Block(kind="quote", text=inner))
                j += 1
            i = j
            continue

        lm = _LIST_RE.match(s)                        # 列表项
        if lm:
            blocks.append(Block(kind="list", marker=lm.group(1), text=lm.group(2)))
            i += 1
            continue

        para: list[str] = [s]                         # 普通段落（连续行合并）
        j = i + 1
        while j < n:
            nxt = lines[j].strip()
            if not nxt or nxt.startswith(("#", "|", ">", "```")) or _RULE_RE.match(nxt) \
                    or _LIST_RE.match(nxt):
                break
            para.append(nxt)
            j += 1
        blocks.append(Block(kind="para", text=" ".join(para)))
        i = j

    return blocks


def _make_table(raw_rows: list[list[str]]) -> Block:
    """把原始表格行整理成 Block（识别分隔行、补齐列数、记录兼容处理）。"""
    delim_idx = -1
    for idx, row in enumerate(raw_rows):
        if row and all(_DELIM_CELL_RE.match(c) for c in row if c != ""):
            delim_idx = idx
            break

    header: list[str] | None = None
    if delim_idx > 0:
        header = raw_rows[0]
        data = raw_rows[1:delim_idx] + raw_rows[delim_idx + 1:]
        if delim_idx > 1:
            COMPAT_NOTES.append(
                f"表格有 {delim_idx} 行表头，已合并为首行：{plain_text(' '.join(header))[:40]}"
            )
            merged = [" ".join(r[c] if c < len(r) else "" for r in raw_rows[:delim_idx]).strip()
                      for c in range(max(len(r) for r in raw_rows[:delim_idx]))]
            header = merged
            data = raw_rows[delim_idx + 1:]
    else:
        data = raw_rows
        COMPAT_NOTES.append(f"表格缺少 | --- | 分隔行，按无表头表格处理：{plain_text(raw_rows[0][0])[:30]}")

    ncols = max(len(r) for r in raw_rows)
    if any(len(r) != ncols for r in raw_rows):
        COMPAT_NOTES.append(f"表格列数不一致，已按 {ncols} 列补齐")
    padded = [list(r) + [""] * (ncols - len(r)) for r in data]
    if header is not None:
        header = list(header) + [""] * (ncols - len(header))
    return Block(kind="table", header=header, rows=padded, ncols=ncols)


# ===================================================================== 字体/XML
def apply_run_format(
    run,
    *,
    cn: str,
    en: str,
    size: float,
    bold: bool = False,
    italic: bool = False,
    color: RGBColor | None = None,
    hint: str | None = None,
) -> None:
    """同一 run 上同时设 ``w:eastAsia`` 与 ``w:ascii``（英文数字走 en，中文走 cn）。"""
    run.bold = bold
    run.italic = italic
    run.font.size = Pt(size)
    run.font.name = en                      # 写入 w:ascii 与 w:hAnsi
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), en)
    rFonts.set(qn("w:hAnsi"), en)
    rFonts.set(qn("w:cs"), en)
    rFonts.set(qn("w:eastAsia"), cn)
    if hint:
        rFonts.set(qn("w:hint"), hint)
    if color is not None:
        run.font.color.rgb = color


def _style_rfonts(style, cn: str, en: str) -> None:
    style.font.name = en                    # 先走 python-docx，保证 rFonts 位置合法
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:ascii"), en)
    rFonts.set(qn("w:hAnsi"), en)
    rFonts.set(qn("w:cs"), en)
    rFonts.set(qn("w:eastAsia"), cn)


def _set_indent(
    paragraph: Paragraph,
    *,
    left_cm: float | None = None,
    right_cm: float | None = None,
    first_line_pt: float | None = None,
    first_line_chars: int | None = None,
) -> None:
    pf = paragraph.paragraph_format
    if left_cm is not None:
        pf.left_indent = Cm(left_cm)
    if right_cm is not None:
        pf.right_indent = Cm(right_cm)
    if first_line_pt is not None:
        pf.first_line_indent = Pt(first_line_pt)
    pPr = paragraph._p.get_or_add_pPr()
    ind = pPr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        pPr.insert_element_before(ind, *_PPR_TAIL[10:])
    if first_line_chars is not None:
        ind.set(qn("w:firstLineChars"), str(first_line_chars))


def _shade(pr, fill: str, successors: Sequence[str]) -> None:
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pr.insert_element_before(shd, *successors)


def _left_border(pPr, color: str, size: int, space: int) -> None:
    pBdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), str(size))
    left.set(qn("w:space"), str(space))
    left.set(qn("w:color"), color)
    pBdr.append(left)
    pPr.insert_element_before(pBdr, "w:shd", *_PPR_TAIL)


def _add_inline_runs(
    paragraph: Paragraph,
    text: str,
    *,
    size: float,
    cn: str = FONT_BODY_CN,
    en: str = FONT_BODY_EN,
    bold: bool = False,
    italic: bool = False,
    color: RGBColor | None = None,
    hint: str | None = None,
) -> None:
    for tok in parse_inline(text):
        run = paragraph.add_run(tok.text)
        apply_run_format(
            run,
            cn=FONT_MONO if tok.code else cn,
            en=FONT_MONO if tok.code else en,
            size=size,
            bold=bold or tok.bold,
            italic=italic or tok.italic,
            color=color,
            hint=hint,
        )


def _add_field(paragraph: Paragraph, instruction: str, cached: str, *, size: float, cn: str,
               en: str) -> None:
    """插入一个 Word 域（begin / instrText / separate / 结果 / end）。"""
    begin = paragraph.add_run()._element
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    begin.append(fld)

    instr_run = paragraph.add_run()._element
    instr = OxmlElement("w:instrText")
    instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    instr.text = instruction
    instr_run.append(instr)

    sep_run = paragraph.add_run()._element
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    sep_run.append(sep)

    result = paragraph.add_run(cached)
    apply_run_format(result, cn=cn, en=en, size=size)

    end_run = paragraph.add_run()._element
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    end_run.append(end)


# ===================================================================== 文档构建
def _setup_styles(doc) -> None:
    normal = doc.styles["Normal"]
    normal.font.size = Pt(BODY_PT)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    _style_rfonts(normal, FONT_BODY_CN, FONT_BODY_EN)
    npf = normal.paragraph_format
    # 规范㈥.4 / ㈦.4：正文行间距为**固定值 22 磅**。
    # 原先写 1.5 倍行距 —— 那不是规范要求的量（1.5 倍随字号变化，
    # 而"固定值 22 磅"是绝对量），形式审查会按 22 磅核对。
    npf.line_spacing = Pt(BODY_LINE_PT)
    npf.space_before = Pt(0)
    npf.space_after = Pt(0)

    for name, size in (("Heading 1", H1_PT), ("Heading 2", H2_PT), ("Heading 3", H3_PT)):
        style = doc.styles[name]
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0, 0, 0)
        _style_rfonts(style, FONT_HEADING, FONT_HEADING)
        pf = style.paragraph_format
        pf.first_line_indent = Pt(0)
        pf.space_before = Pt(12)
        pf.space_after = Pt(6)
        pf.line_spacing = Pt(BODY_LINE_PT)
        pf.keep_with_next = True


def _rich_paragraph(
    doc,
    text: str,
    *,
    size: float = BODY_PT,
    cn: str = FONT_BODY_CN,
    en: str = FONT_BODY_EN,
    bold: bool = False,
    align: int | None = None,
    style: str | None = None,
    line_spacing: float | "Length" = Pt(BODY_LINE_PT),
    space_before: float = 0.0,
    space_after: float = 0.0,
    color: RGBColor | None = None,
) -> Paragraph:
    """通用段落。

    !! 行距默认值必须是「固定值 22 磅」，不能是 1.5 !!
      规范㈥.4 / ㈦.4 要求正文行间距为**固定值 22 磅**。
      但本函数会给段落写**直接格式**（paragraph_format），
      而直接格式的优先级高于样式 —— 所以哪怕 Normal 样式已经设成 22 磅，
      只要这里默认写 1.5，正文段落的实际行距就还是 1.5。
      实测证据（导出自检）：Normal 段落里出现
        line=360 rule=auto 293 段（=1.5 倍）与 line=440 rule=exact 180 段（=22 磅）
      两种并存 —— 前者就是被这个默认值带偏的。
      调用方若确实需要别的行距（封面标题、页脚等），显式传参即可。
    """
    paragraph = doc.add_paragraph(style=style)
    pf = paragraph.paragraph_format
    pf.line_spacing = line_spacing
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    if align is not None:
        paragraph.alignment = align
    _add_inline_runs(paragraph, text, size=size, cn=cn, en=en, bold=bold, color=color)
    return paragraph


def _body_paragraph(doc, text: str) -> Paragraph:
    """正文段：宋体小四、行距固定值 22 磅、首行缩进 2 字符，英文数字 Times New Roman。"""
    paragraph = _rich_paragraph(doc, text)
    _set_indent(paragraph, left_cm=0.0, right_cm=0.0, first_line_pt=2 * BODY_PT,
                first_line_chars=FIRST_LINE_CHARS)
    return paragraph


def _list_paragraph(doc, marker: str, text: str, *, extra_left_cm: float = 0.0) -> Paragraph:
    paragraph = doc.add_paragraph()
    pf = paragraph.paragraph_format
    pf.line_spacing = Pt(BODY_LINE_PT)
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    # 悬挂缩进：编号顶到左边距，折行与文字左对齐（参考文献条目同此处理）
    _set_indent(paragraph, left_cm=HANGING_CM + extra_left_cm, right_cm=0.0,
                first_line_pt=-HANGING_CM * CM_TO_PT)
    _add_inline_runs(paragraph, f"{marker} {text}", size=BODY_PT)
    return paragraph


def _quote_paragraph(doc, text: str, *, marker: str = "", indent_cm: float = QUOTE_LEFT_CM) -> Paragraph:
    paragraph = doc.add_paragraph()
    pf = paragraph.paragraph_format
    pf.line_spacing = Pt(BODY_LINE_PT)
    pf.space_before = Pt(3)
    pf.space_after = Pt(3)
    _set_indent(paragraph, left_cm=indent_cm, right_cm=0.0, first_line_pt=0.0,
                first_line_chars=0)
    pPr = paragraph._p.get_or_add_pPr()
    _left_border(pPr, NOTE_BORDER, 18, 8)
    if marker:
        _add_inline_runs(paragraph, f"{marker} {text}", size=BODY_PT)
    else:
        _add_inline_runs(paragraph, text, size=BODY_PT)
    return paragraph


_CODE_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def _code_line_tokens(line: str) -> list[tuple[str, bool]]:
    """代码行里若混入了 ``**`` 强调，转成 (文本, 是否加粗)，避免 Markdown 残留。"""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _CODE_BOLD_RE.finditer(line):
        if m.start() > pos:
            out.append((line[pos:m.start()], False))
        out.append((m.group(1), True))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], False))
    if not out:
        out = [("", False)]
    return out


def _split_by_width(text: str) -> list[tuple[str, bool]]:
    """按显示宽度把文本切成（片段, 是否全角）——全角片段用中文字体与大一号字。"""
    out: list[tuple[str, bool]] = []
    buf = ""
    cur: bool | None = None
    for ch in text:
        wide = unicodedata.east_asian_width(ch) in ("W", "F")
        if cur is None or wide == cur:
            buf += ch
        else:
            out.append((buf, bool(cur)))
            buf = ch
        cur = wide
    if buf:
        out.append((buf, bool(cur)))
    return out


def _code_block(doc, lines: list[str]) -> None:
    """代码/命令块：Consolas、9 pt、不缩进、浅灰底纹 + 左侧竖线。"""
    if not lines:
        lines = [""]
    last = len(lines) - 1
    for idx, raw in enumerate(lines):
        paragraph = doc.add_paragraph()
        pf = paragraph.paragraph_format
        pf.line_spacing = Pt(CODE_LINE_PT)
        pf.space_before = Pt(3) if idx == 0 else Pt(0)
        pf.space_after = Pt(3) if idx == last else Pt(0)
        _set_indent(paragraph, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)
        pPr = paragraph._p.get_or_add_pPr()
        _left_border(pPr, CODE_BORDER, 12, 6)
        _shade(pPr, CODE_FILL, _PPR_TAIL)

        # 不在字符级断行（代码里的长标识符不会被拦腰截断），且不受文档网格约束
        word_wrap = OxmlElement("w:wordWrap")
        word_wrap.set(qn("w:val"), "0")
        pPr.insert_element_before(word_wrap, "w:overflowPunct", *_PPR_TAIL[4:])
        for tag in ("w:autoSpaceDE", "w:autoSpaceDN"):
            el = OxmlElement(tag)
            el.set(qn("w:val"), "0")
            pPr.insert_element_before(el, "w:bidi", *_PPR_TAIL[9:])
        snap = OxmlElement("w:snapToGrid")
        snap.set(qn("w:val"), "0")
        pPr.insert_element_before(snap, "w:spacing", *_PPR_TAIL[11:])

        if "**" in raw:
            COMPAT_NOTES.append("代码块内混入 ** 强调标记，已转为加粗文本（不保留字面 **）")
        for text, bold in _code_line_tokens(raw):
            for segment, wide in _split_by_width(text):
                run = paragraph.add_run(segment)
                apply_run_format(
                    run,
                    cn=FONT_BODY_CN,
                    en=FONT_BODY_CN if wide else FONT_MONO,
                    size=CODE_CJK_PT if wide else CODE_PT,
                    bold=bold,
                    hint=None if wide else "default",
                )


def _empty_paragraph(doc) -> None:
    paragraph = doc.add_paragraph()
    pf = paragraph.paragraph_format
    pf.line_spacing = 1.0
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    _set_indent(paragraph, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)
    run = paragraph.add_run("")
    apply_run_format(run, cn=FONT_BODY_CN, en=FONT_BODY_EN, size=BODY_PT)


def _column_widths(block: Block) -> list[float]:
    """按内容宽度按比例分配列宽，并保证最小列宽（列多时最小列宽自动收窄）。"""
    rows: list[list[str]] = ([block.header] if block.header else []) + block.rows
    weights: list[float] = []
    for c in range(block.ncols):
        cells = [_display_cells(r[c]) for r in rows if c < len(r) and r[c]]
        weights.append(min(max(max(cells, default=4), 8), 46))
    total = sum(weights)
    # 最小列宽：窄标签列不至于一个字一折行；同时保证所有最小列宽合计不超过版心 60%，
    # 否则 6~7 列的表会把正文列挤成"每行两三个字"。
    min_cm = min(2.0, TEXT_WIDTH_CM * 0.6 / block.ncols)
    widths = [TEXT_WIDTH_CM * w / total for w in weights]
    for _ in range(8):
        deficit = sum(max(0.0, min_cm - w) for w in widths)
        if deficit <= 1e-6:
            break
        pool = sum(w - min_cm for w in widths if w > min_cm)
        if pool <= 1e-6:
            break
        widths = [w - (w - min_cm) / pool * deficit if w > min_cm else w for w in widths]
        widths = [max(w, min_cm) for w in widths]
    scale = TEXT_WIDTH_CM / sum(widths)
    return [w * scale for w in widths]


def _fill_cell(cell, text: str, *, bold: bool, align: int | None = None) -> None:
    paragraph = cell.paragraphs[0]
    pf = paragraph.paragraph_format
    pf.line_spacing = 1.0
    pf.space_before = Pt(1)
    pf.space_after = Pt(1)
    if align is not None:
        paragraph.alignment = align
    _set_indent(paragraph, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)
    _add_inline_runs(paragraph, text, size=TABLE_PT, bold=bold)


def _table_block(doc, block: Block) -> None:
    """真正的 Word 表格：Table Grid 边框、表头加粗+浅灰底纹、跨页时重复表头。"""
    all_rows: list[list[str]] = ([block.header] if block.header else []) + block.rows
    table: Table = doc.add_table(rows=len(all_rows), cols=block.ncols)
    table.style = "Table Grid"
    table.autofit = False

    widths = _column_widths(block)
    tblPr = table._tbl.tblPr
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.insert_element_before(tblW, *_TBLPR_TAIL)
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(int(round(sum(widths) * 567))))
    for idx, width in enumerate(widths):
        table.columns[idx].width = Cm(width)

    for ri, row_data in enumerate(all_rows):
        is_header = block.header is not None and ri == 0
        for ci in range(block.ncols):
            cell = table.cell(ri, ci)
            cell.width = Cm(widths[ci])
            _fill_cell(cell, row_data[ci] if ci < len(row_data) else "", bold=is_header,
                       align=WD_ALIGN_PARAGRAPH.CENTER if is_header else None)
        if is_header:
            trPr = table.rows[ri]._tr.get_or_add_trPr()
            if trPr.find(qn("w:tblHeader")) is None:
                header_el = OxmlElement("w:tblHeader")
                header_el.set(qn("w:val"), "true")
                trPr.insert_element_before(header_el, *_TRPR_TAIL)
            for cell in table.rows[ri].cells:
                tcPr = cell._tc.get_or_add_tcPr()
                _shade(tcPr, HEADER_FILL, _TCPR_TAIL)


def _heading_paragraph(doc, block: Block) -> Paragraph:
    level = min(max(block.level, 1), 3)
    paragraph = doc.add_paragraph(style=f"Heading {level}")
    pf = paragraph.paragraph_format
    pf.keep_with_next = True
    pf.line_spacing = Pt(BODY_LINE_PT)
    pf.space_before = Pt(18 if level == 1 else 12)
    pf.space_after = Pt(12 if level == 1 else 6)
    _set_indent(paragraph, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)
    if level == 1:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pf.page_break_before = True
    _add_inline_runs(paragraph, block.text, size=(H1_PT, H2_PT, H3_PT)[level - 1],
                     cn=FONT_HEADING, en=FONT_HEADING, bold=True,
                     color=RGBColor(0, 0, 0))
    return paragraph


def _cover(doc) -> None:
    """封面：严格按《湖南工商大学本科生毕业论文（设计）撰写规范》㈡ + 封面样式模板。

    模板原文（`封面样式.docx`）的表格是 **7 行 × 2 列**：

        题    目 | （空）
        学生姓名 | （空）
        学    号 | （空）
        学    院 | （空）
        专业班级 | （空）
        指导教师 | （空）
        职    称 | （空）

    末尾另起一行是「年      月」。

    !! 为什么改成"表格"而不是原先的"作者/学号/导师/日期 四行文字" !!
    !!   学校模板是**表格**；形式审查按模板逐项核对，少一项（学院/专业班级/职称）
    !!   或多出一项（"作者""导师"都不是模板用词）都会被要求返工。
    !!   所以这里把字段名与顺序**照抄模板**，不自己发明。
    """
    def blank(count: int) -> None:
        for _ in range(count):
            _empty_paragraph(doc)

    blank(4)
    _rich_paragraph(doc, "湖南工商大学", size=26, cn=FONT_HEADING, en=FONT_HEADING, bold=True,
                    align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.0)
    _rich_paragraph(doc, "本科毕业设计（论文）", size=20, cn=FONT_HEADING, en=FONT_HEADING,
                    bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.0)
    blank(4)
    _rich_paragraph(doc, _COVER_MAIN, size=22, cn=FONT_HEADING, en=FONT_HEADING, bold=True,
                    align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.5)
    if _COVER_SUB:
        _rich_paragraph(doc, _COVER_SUB, size=16, cn=FONT_HEADING, en=FONT_HEADING, bold=True,
                        align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.5)
    blank(3)

    # 字段名照抄学校模板（含模板里的空格排版），值为占位符
    cover_rows = (
        ("题    目", _COVER_MAIN),
        ("学生姓名", COVER_FIELDS.get("name", "")),
        ("学    号", COVER_FIELDS.get("sid", "")),
        ("学    院", COVER_FIELDS.get("college", "")),
        ("专业班级", COVER_FIELDS.get("cls", "")),
        ("指导教师", COVER_FIELDS.get("teacher", "")),
        ("职    称", COVER_FIELDS.get("title", "")),
    )
    table = doc.add_table(rows=len(cover_rows), cols=2)
    table.style = "Table Grid"
    table.autofit = False
    # 左列放字段名（3.2cm），右列放内容（其余宽度）
    label_w, value_w = 3.2, TEXT_WIDTH_CM - 3.2
    for ri, (label, value) in enumerate(cover_rows):
        row = table.rows[ri]
        row.height = Cm(1.0)
        for ci, (cell, text) in enumerate(zip(row.cells, (label, value))):
            cell.width = Cm(label_w if ci == 0 else value_w)
            para = cell.paragraphs[0]
            para.alignment = (WD_ALIGN_PARAGRAPH.CENTER if ci == 0
                              else WD_ALIGN_PARAGRAPH.CENTER)
            para.paragraph_format.line_spacing = 1.0
            _set_indent(para, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)
            _add_inline_runs(para, text or "", size=14.0,
                             cn=FONT_BODY_CN, en=FONT_BODY_EN,
                             bold=(ci == 0))
    blank(2)
    _rich_paragraph(doc, COVER_FIELDS.get("date", "年      月"), size=14,
                    align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.0)


def _integrity_page(doc) -> None:
    """诚信声明（规范二㈢.2：排在封面之后、摘要之前，**页脚不标页码**）。

    正文照抄学校模板（`1.湖南工商大学本科生毕业论文管理规范汇总` 第 498-501 行），
    只把"作者签名/日期"留空待手写 —— 声明内容一个字都不能改。
    """
    _rich_paragraph(doc, "湖南工商大学本科毕业论文（设计）诚信声明", size=16,
                    cn=FONT_HEADING, en=FONT_HEADING, bold=True,
                    align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.5,
                    space_before=12, space_after=12)
    _rich_paragraph(
        doc,
        "本人郑重声明：所呈交的本科毕业论文（设计）"
        "《基于 Lakehouse 与 AI Agent 的批流一体智能数据分析平台》"
        "是本人在指导老师的指导下，独立进行研究工作所取得的成果，成果不存在知识产权争议，"
        "除文中已经注明引用的内容外，本论文（设计）不含任何其他个人或集体已经发表或撰写过的"
        "作品成果。对本文的研究做出重要贡献的个人和集体均已在文中以明确方式标明。"
        "本人完全意识到本声明的法律结果由本人承担。",
        size=BODY_PT, line_spacing=Pt(BODY_LINE_PT),
    )
    for _ in range(3):
        _empty_paragraph(doc)
    _rich_paragraph(doc, "作者签名：________________", size=BODY_PT,
                    align=WD_ALIGN_PARAGRAPH.RIGHT, line_spacing=1.5)
    _rich_paragraph(doc, "日期：        年     月     日", size=BODY_PT,
                    align=WD_ALIGN_PARAGRAPH.RIGHT, line_spacing=1.5)


def _toc_page(doc, notes: list[Block]) -> None:
    _rich_paragraph(doc, "目录", size=16, cn=FONT_HEADING, en=FONT_HEADING, bold=True,
                    align=WD_ALIGN_PARAGRAPH.CENTER, line_spacing=1.5,
                    space_before=6, space_after=6)
    hint = _rich_paragraph(
        doc,
        "提示：本页目录是 Word 域（" + TOC_INSTR + "），不在磁盘上缓存页码。"
        "打开文档后请把光标放在目录上按 F9（或 Ctrl+A 全选后按 F9 / 右键选择“更新域”），"
        "在弹窗中选择“更新整个目录”，即可生成带页码的目录；修改正文后同样按 F9 刷新。",
        size=10.5, align=WD_ALIGN_PARAGRAPH.LEFT, line_spacing=1.5, space_after=6,
        color=RGBColor(0x59, 0x59, 0x59),
    )
    _set_indent(hint, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)

    toc = doc.add_paragraph()
    toc.paragraph_format.line_spacing = 1.5
    _set_indent(toc, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)
    _add_field(toc, " " + TOC_INSTR + " ", "（目录域：按 F9 生成，含一至三级标题与页码）",
               size=BODY_PT, cn=FONT_BODY_CN, en=FONT_BODY_EN)

    _empty_paragraph(doc)
    for block in notes:
        if block.kind == "quote":
            _quote_paragraph(doc, block.text)
        elif block.kind == "para":
            _body_paragraph(doc, block.text)


def _body(doc, blocks: list[Block]) -> None:
    previous: str = ""
    for block in blocks:
        if block.kind == "rule":
            continue
        if block.kind == "heading":
            paragraph = _heading_paragraph(doc, block)
            if block.level == 2 and plain_text(block.text) in ("摘要", "Abstract"):
                paragraph.paragraph_format.page_break_before = True
        elif block.kind == "para":
            _body_paragraph(doc, block.text)
        elif block.kind == "list":
            _list_paragraph(doc, block.marker, block.text)
        elif block.kind == "quote":
            _quote_paragraph(doc, block.text)
        elif block.kind == "quote_list":
            _quote_paragraph(doc, block.text, marker=block.marker)
        elif block.kind == "code":
            _code_block(doc, block.lines)
        elif block.kind == "table":
            if previous not in ("", "table"):
                pass
            _empty_paragraph(doc)          # 表前空段
            _table_block(doc, block)
            _empty_paragraph(doc)          # 表后空段
        previous = block.kind


def _rich_paragraph_into(paragraph: Paragraph, text: str, *, size: float,
                         cn: str = FONT_BODY_CN, en: str = FONT_BODY_EN,
                         bold: bool = False) -> None:
    """把一段文字追加进**已有**段落（用于页眉/页脚）。

    为什么需要它：`_rich_paragraph` 内部会 `doc.add_paragraph()` 新建段落，
    而页眉页脚的第 0 段是 Word 预置好的 —— 再新建就会多出一个空段，
    表现为"页眉上方多一条空白行"。所以这里只做 run 级追加。
    """
    _add_inline_runs(paragraph, text, size=size, cn=cn, en=en, bold=bold)


def _setup_sections(doc) -> None:
    for section in doc.sections:
        section.page_width = Cm(PAGE_W_CM)
        section.page_height = Cm(PAGE_H_CM)
        section.top_margin = Cm(MARGIN_TOP_CM)
        section.bottom_margin = Cm(MARGIN_BOTTOM_CM)
        section.left_margin = Cm(MARGIN_LEFT_CM)
        section.right_margin = Cm(MARGIN_RIGHT_CM)
        section.header_distance = Cm(1.5)
        section.footer_distance = Cm(1.75)

    # ------------------------------------------------------------
    # 页眉：规范㈦.10「页眉设计为"湖南工商大学毕业论文（设计）"，页脚设计为
    #       "第 页 共 页"式样，均居中设置，小五号宋体，从论文（设计）正文
    #       所在页开始计算」
    #
    # !! "从正文所在页开始计算"要落到 section 上，而不是靠"某一页不写" !!
    #   本文件的节划分是：封面节 / 摘要节 / 正文节。因此页眉页脚只在
    #   **正文节**设置，前面两节显式 is_linked_to_previous = False 且留空 ——
    #   否则 Word 会把正文节的页眉页脚**向前继承**，摘要页也会长出页眉。
    # ------------------------------------------------------------
    body_section = doc.sections[-1]
    body_section.header.is_linked_to_previous = False
    header_para = body_section.header.paragraphs[0]
    header_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    header_para.paragraph_format.line_spacing = 1.0
    _set_indent(header_para, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)
    _rich_paragraph_into(header_para, "湖南工商大学毕业论文（设计）", size=FOOTER_PT,
                         cn=FONT_BODY_CN, en=FONT_BODY_EN)

    body_section.footer.is_linked_to_previous = False
    footer_para = body_section.footer.paragraphs[0]
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_para.paragraph_format.line_spacing = 1.0
    _set_indent(footer_para, left_cm=0.0, right_cm=0.0, first_line_pt=0.0, first_line_chars=0)
    # 页脚样式：「第 X 页 共 Y 页」（规范原文式样），X/Y 都是 Word 域
    _rich_paragraph_into(footer_para, "第 ", size=FOOTER_PT, cn=FONT_BODY_CN, en=FONT_BODY_EN)
    _add_field(footer_para, " PAGE ", "1", size=FOOTER_PT, cn=FONT_BODY_CN, en=FONT_BODY_EN)
    _rich_paragraph_into(footer_para, " 页 共 ", size=FOOTER_PT, cn=FONT_BODY_CN, en=FONT_BODY_EN)
    _add_field(footer_para, " NUMPAGES ", "1", size=FOOTER_PT, cn=FONT_BODY_CN, en=FONT_BODY_EN)
    _rich_paragraph_into(footer_para, " 页", size=FOOTER_PT, cn=FONT_BODY_CN, en=FONT_BODY_EN)

    # 前置节（封面 / 摘要）不继承正文节的页眉页脚。
    # 规范㈡ 说封面"按照学校统一规定的封面样式打印"，摘要页页码用罗马数字，
    # 这里先做到"不出现正文页眉页脚"；罗马页码由 _roman_front_matter 处理。
    for section in doc.sections[:-1]:
        section.header.is_linked_to_previous = False
        section.footer.is_linked_to_previous = False
        for p in list(section.header.paragraphs):
            p.text = ""
        for p in list(section.footer.paragraphs):
            p.text = ""

    sectPr = body_section._sectPr
    pg_num = OxmlElement("w:pgNumType")
    pg_num.set(qn("w:start"), "1")
    sectPr.insert_element_before(pg_num, *_SECTPR_TAIL)


def _enable_update_fields(doc) -> None:
    settings = doc.settings.element
    if settings.find(qn("w:updateFields")) is not None:
        return
    el = OxmlElement("w:updateFields")
    el.set(qn("w:val"), "true")
    for tag in _SETTINGS_BEFORE:
        node = settings.find(qn(tag))
        if node is not None:
            node.addprevious(el)
            return
    settings.append(el)


_COVER_MAIN = ""
_COVER_SUB = ""

# ---------------------------------------------------------------- 封面/声明 填表信息
# 由学生本人提供，来源为答辩与附件材料填报；改这里即可同时更新
# 封面表格、诚信声明落款等所有需要这一组信息的位置。
COVER_FIELDS: dict[str, str] = {
    "name": "蒋树阳",
    "sid": "2325010007",
    "college": "前沿交叉学院",
    "cls": "大数据2303",
    "teacher": "陈杰",
    "title": "副教授",
    "grade": "2027",          # 届别
    "date": "2027 年   月",   # 封面底部「年  月」
}


def build_document(blocks: list[Block], title: str) -> "Document":
    """组装整个文档。

    !! 装订顺序按《湖南工商大学本科生毕业论文（设计）撰写规范》三㈢ !!
        1.封面  2.中文摘要及关键词  3.英文摘要及关键词  4.目录  5.正文
        6.参考文献  7.符号说明  8.附录  9.致谢
        （另据二㈢.2：**诚信声明排在封面之后、摘要之前，且页脚不标页码**）

    !! 与原先实现的差别 !!
       原先的顺序是「封面 → 目录 → 正文」，把摘要当普通正文段落；
       而规范要求摘要在目录**之前**、且中英文摘要各自单独成页。
       所以这里改成：封面节 → 诚信声明 → [中文摘要节] → [英文摘要节] → 正文节，
       每个节点用 `add_section(NEW_PAGE)` 真正**分节**（不是靠空段落顶开），
       这样"页眉页脚从正文页开始"才有可挂靠的节边界。
    """
    global _COVER_MAIN, _COVER_SUB
    _COVER_MAIN, _COVER_SUB = _split_cover_title(title)

    doc = Document()
    _setup_styles(doc)

    # ---- ① 封面 ----
    _cover(doc)
    doc.add_section(WD_SECTION.NEW_PAGE)

    # ---- ② 诚信声明（规范要求：页脚不标页码）----
    _integrity_page(doc)

    # ---- 定位正文章节：首个 "N. " 形式的一级标题（工科编号）----
    #   兼容两种写法：新的 "1. 绪论" 与旧的 "第 1 章 绪论"，
    #   这样即使正文还没改编号，也不会把摘要当成正文起点。
    def _is_body_chapter(b: "Block") -> bool:
        if b.kind != "heading" or b.level != 1:
            return False
        return bool(re.match(r"^\s*(第\s*\d+\s*章|\d+[\.、]\s*\S)", b.text or ""))

    body_idx = next((i for i, b in enumerate(blocks) if _is_body_chapter(b)), -1)

    # 首个 "#" 一级标题是报告题目（已上封面，正文不再重复）；
    # 其后的引用块是"草稿说明"，放到目录页。
    title_idx = next(
        (i for i, b in enumerate(blocks) if b.kind == "heading" and b.level == 1), -1
    )
    start = title_idx + 1 if title_idx >= 0 else 0
    notes: list[Block] = []
    scan = start
    while scan < len(blocks) and not _is_body_chapter(blocks[scan]):
        if blocks[scan].kind in ("quote", "para"):
            notes.append(blocks[scan])
        scan += 1

    front = blocks[start:body_idx] if body_idx >= 0 else []
    body = blocks[body_idx:] if body_idx >= 0 else blocks[start:]

    # ---- ③④ 摘要：中文摘要 / 英文摘要 各自单独成页 ----
    #   规范㈣.10「中文摘要与关键词单独为一页」、㈤.8「英文摘要与关键词单独为一页」。
    abs_split = next(
        (i for i, b in enumerate(front)
         if b.kind == "heading" and re.match(r"^\s*(ABSTRACT|Abstract)\s*$", b.text or "")),
        -1,
    )
    zh = front[:abs_split] if abs_split >= 0 else front
    en = front[abs_split:] if abs_split >= 0 else []

    if zh:
        _body(doc, zh)
    if en:
        doc.add_section(WD_SECTION.NEW_PAGE)
        _body(doc, en)

    # ---- ⑤ 目录 ----
    doc.add_section(WD_SECTION.NEW_PAGE)
    _toc_page(doc, notes)

    # ---- ⑥~⑨ 正文（含参考文献/附录/致谢，它们本身是正文的一级标题）----
    doc.add_section(WD_SECTION.NEW_PAGE)
    _body(doc, body)

    _setup_sections(doc)
    _enable_update_fields(doc)
    return doc


def _split_cover_title(title: str) -> tuple[str, str]:
    """封面主标题/副标题：形如“基于 X 的 Y”时，把“基于 X”作为副标题，其余作为题目。"""
    m = re.match(r"^(基于.+?)\s*的\s*(.+)$", title)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    return title.strip(), "______"


# ===================================================================== 校验
def _is_code_paragraph(paragraph: Paragraph) -> bool:
    pPr = paragraph._p.find(qn("w:pPr"))
    if pPr is None:
        return False
    shd = pPr.find(qn("w:shd"))
    return shd is not None and shd.get(qn("w:fill")) == CODE_FILL


def _run_is_inline_code(run) -> bool:
    rPr = run._element.find(qn("w:rPr"))
    rFonts = rPr.find(qn("w:rFonts")) if rPr is not None else None
    return rFonts is not None and rFonts.get(qn("w:ascii")) == FONT_MONO


def _split_paragraph_text(paragraph: Paragraph) -> tuple[str, str]:
    """返回（行内代码以外的文本, 行内代码文本）。"""
    plain: list[str] = []
    code: list[str] = []
    for run in paragraph.runs:
        (code if _run_is_inline_code(run) else plain).append(run.text)
    return "".join(plain), "".join(code)


def verify(path: Path, sample: int = 200) -> None:
    """重新打开生成的文件做冒烟检查 + 断言 + 版式自检。"""
    doc = Document(str(path))
    paragraphs = doc.paragraphs
    headings = {1: 0, 2: 0, 3: 0}
    for paragraph in paragraphs:
        name = paragraph.style.name if paragraph.style is not None else ""
        if name in ("Heading 1", "Heading 2", "Heading 3"):
            headings[int(name[-1])] += 1

    tables = doc.tables
    LOG.info("── 冒烟检查（重新打开 %s）", path.name)
    LOG.info("段落总数        : %d", len(paragraphs))
    LOG.info("Heading 1       : %d", headings[1])
    LOG.info("Heading 2       : %d", headings[2])
    LOG.info("Heading 3       : %d", headings[3])
    LOG.info("表格数量        : %d", len(tables))
    LOG.info("表格总行数      : %d", sum(len(t.rows) for t in tables))
    LOG.info("节数量          : %d", len(doc.sections))

    # ---- 域：目录域 + 页脚页码域 ----
    toc_field = any(
        "instrText" in p._p.xml and re.search(r"TOC\s+\\o", p._p.xml or "") for p in paragraphs
    )
    footer_xml = doc.sections[-1].footer.paragraphs[0]._p.xml
    page_field = "instrText" in footer_xml and "PAGE" in footer_xml
    update_fields = doc.settings.element.find(qn("w:updateFields")) is not None
    LOG.info("目录 TOC 域     : %s（updateFields=%s）", toc_field, update_fields)
    LOG.info("页脚 PAGE 域    : %s", page_field)
    assert toc_field, "没有插入 TOC 目录域"
    assert page_field, "页脚没有 PAGE 页码域"
    assert len(tables) > 0, "文档里没有表格"
    assert headings[1] > 5, f"Heading 1 数量不足：{headings[1]}"

    # ---- 页面：A4 + 页边距（四边不对称，按学校规范逐边断言）----
    section = doc.sections[-1]
    LOG.info("页面 A4         : %.1f x %.1f cm", section.page_width.cm, section.page_height.cm)
    LOG.info("页边距          : 上 %.2f / 下 %.2f / 左 %.2f / 右 %.2f cm",
             section.top_margin.cm, section.bottom_margin.cm,
             section.left_margin.cm, section.right_margin.cm)
    assert abs(section.page_width.cm - PAGE_W_CM) < 0.05
    assert abs(section.page_height.cm - PAGE_H_CM) < 0.05
    # 规范㈣.9：上 30mm / 下 25mm / 左 30mm / 右 20mm —— 四条边都要核
    assert abs(section.top_margin.cm - MARGIN_TOP_CM) < 0.02, "上边距不符合 30mm"
    assert abs(section.bottom_margin.cm - MARGIN_BOTTOM_CM) < 0.02, "下边距不符合 25mm"
    assert abs(section.left_margin.cm - MARGIN_LEFT_CM) < 0.02, "左边距不符合 30mm"
    assert abs(section.right_margin.cm - MARGIN_RIGHT_CM) < 0.02, "右边距不符合 20mm"

    # ---- 页眉页脚：规范㈦.10 要求页眉「湖南工商大学毕业论文（设计）」、
    #      页脚「第 页 共 页」----
    header_txt = "".join(p.text for p in section.header.paragraphs)
    footer_txt = "".join(p.text for p in section.footer.paragraphs)
    LOG.info("页眉            : %r", header_txt.strip())
    LOG.info("页脚            : %r", footer_txt.strip())
    assert "湖南工商大学毕业论文（设计）" in header_txt, "页眉缺少学校规定的文字"
    assert "第" in footer_txt and "页" in footer_txt and "共" in footer_txt, \
        "页脚不符合「第 页 共 页」式样"

    # ---- 前置部分不得带正文页眉（规范：页眉从正文页开始）----
    front_sections = doc.sections[:-1]
    leaked = [i for i, s in enumerate(front_sections)
              if "湖南工商大学毕业论文（设计）" in "".join(p.text for p in s.header.paragraphs)]
    assert not leaked, f"前置节 {leaked} 泄漏了正文页眉"

    # ---- 表格：Table Grid / 表头重复 / 底纹 ----
    # !! 封面表与数据表是**两类**表，判据必须分开 !!
    #   封面那张表是学校模板的"题目/学生姓名/…"表单：只有 1 页、没有表头概念，
    #   **不应**设置 tblHeader（设了会在换页时重复整行）。
    #   正文里的数据表才要求"跨页重复表头 + 表头底纹"。
    #   原先的断言 `repeats == len(tables)` 把两类混在一起比，
    #   加了封面表之后必然失败 —— 而失败的原因是"封面表不该重复"，
    #   那是**正确的行为**，所以要改的是判据，不是封面表。
    def _is_cover_table(t) -> bool:
        if len(t.columns) != 2 or len(t.rows) != 7:
            return False
        return t.cell(0, 0).text.strip().replace(" ", "") == "题目"

    data_tables = [t for t in tables if not _is_cover_table(t)]
    cover_tables = [t for t in tables if _is_cover_table(t)]
    LOG.info("封面表          : %d 张（1 页表单，不要求跨页重复表头）", len(cover_tables))

    repeats = 0
    shaded = 0
    for table in data_tables:
        trPr = table.rows[0]._tr.find(qn("w:trPr"))
        if trPr is not None and trPr.find(qn("w:tblHeader")) is not None:
            repeats += 1
        tcPr = table.cell(0, 0)._tc.find(qn("w:tcPr"))
        shd = tcPr.find(qn("w:shd")) if tcPr is not None else None
        if shd is not None and shd.get(qn("w:fill")) == HEADER_FILL:
            shaded += 1
    grid = sum(1 for t in tables if t.style is not None and t.style.name == "Table Grid")
    LOG.info("表格样式/表头  : Table Grid %d/%d，重复表头 %d/%d，表头底纹 %d/%d",
             grid, len(tables), repeats, len(data_tables), shaded, len(data_tables))
    assert grid == len(tables), "有表格没有套用 Table Grid"
    assert repeats == len(data_tables), "存在未设置 tblHeader 的数据表表头"

    # ---- 正文版式：**行距固定值 22 磅** + 首行缩进 2 字符 + 宋体/Times New Roman ----
    #   规范㈥.4 / ㈦.4 写的是"行间距为固定值 22 磅"。
    #   Word 用 w:spacing 的 w:line + w:lineRule 表达行距：
    #     固定值 22 磅 → line="440"（22 × 20 = 440 twips）+ lineRule="exact"
    #     1.5 倍行距    → line="360" + lineRule="auto"
    #
    # !! 判据的**起点必须是正文第一页**，不能从"摘要"或"目录"开始 !!
    #   规范㈣.10 要求"中文摘要与关键词单独为一页"、目录页也有自己的排版
    #   （目录条目按规范㈥.2 用"固定值 22"，但页首的 TOC 域与说明文字不是正文段落）。
    #   实测踩到的坑：把起点设在"摘要"时，目录页的 3 个段落（TOC 域、提示语、
    #   目录标题）被算进正文，计数 462/465 恒不相等 —— 而它们**本来就不该按正文行距**。
    #   所以这里把起点定位到"第一个一级标题"（正文第一章），逐段核到文末。
    body_from = 0
    for i, p in enumerate(paragraphs):
        if p.style is not None and p.style.name == "Heading 1" and p.text.strip():
            body_from = i
            break
    LOG.info("正文起点        : 第 %d 段（%r）", body_from, paragraphs[body_from].text.strip()[:30]
             if body_from < len(paragraphs) else "")
    expect_line = str(int(round(BODY_LINE_PT * 20)))   # 22 磅 → 440
    spacing_ok = indent_ok = font_ok = body_count = code_count = 0
    spacing_bad: list[str] = []
    for paragraph in paragraphs[body_from:]:
        if _is_code_paragraph(paragraph):
            code_count += 1
            continue
        if not paragraph.text.strip():
            continue
        if paragraph.style.name == "Normal":
            body_count += 1
            pPr = paragraph._p.find(qn("w:pPr"))
            spacing = pPr.find(qn("w:spacing")) if pPr is not None else None
            ind = pPr.find(qn("w:ind")) if pPr is not None else None
            if spacing is not None and spacing.get(qn("w:line")) == expect_line \
                    and spacing.get(qn("w:lineRule")) == "exact":
                spacing_ok += 1
            elif len(spacing_bad) < 5:
                spacing_bad.append(paragraph.text.strip()[:30])
            if ind is not None and ind.get(qn("w:firstLineChars")) == str(FIRST_LINE_CHARS):
                indent_ok += 1
        for run in paragraph.runs:
            rPr = run._element.find(qn("w:rPr"))
            rFonts = rPr.find(qn("w:rFonts")) if rPr is not None else None
            if rFonts is not None and rFonts.get(qn("w:ascii")) == FONT_BODY_EN \
                    and rFonts.get(qn("w:eastAsia")) == FONT_BODY_CN:
                font_ok += 1
                break
    LOG.info("正文段落        : %d 个（Normal 非空），其中行距固定值 %.0f 磅 %d、首行缩进 2 字符 %d",
             body_count, BODY_LINE_PT, spacing_ok, indent_ok)
    LOG.info("代码块段落      : %d", code_count)
    if spacing_bad:
        LOG.warning("行距不符的段落样例：%s", " | ".join(spacing_bad))
    assert body_count > 0 and spacing_ok == body_count, \
        f"有正文段落不是行距固定值 {BODY_LINE_PT:.0f} 磅（规范要求）"
    assert indent_ok > 0, "没有任何段落设置首行缩进 2 字符"

    # ---- 列表与参考文献：悬挂缩进 ----
    hanging_count = 0
    hanging_bad = 0
    expected_hanging = int(round(Cm(HANGING_CM).twips))
    for paragraph in paragraphs:
        pPr = paragraph._p.find(qn("w:pPr"))
        ind = pPr.find(qn("w:ind")) if pPr is not None else None
        if ind is None:
            continue
        hanging = ind.get(qn("w:hanging"))
        if hanging is None:
            continue
        hanging_count += 1
        if abs(int(hanging) - expected_hanging) > 2:
            hanging_bad += 1
    LOG.info("悬挂缩进段落    : %d 段（hanging≈%d twips = %.2f cm），异常 %d",
             hanging_count, expected_hanging, HANGING_CM, hanging_bad)
    assert hanging_count > 0 and hanging_bad == 0, "列表/参考文献的悬挂缩进不正确"

    # ---- Markdown 残留：全篇扫描 ----
    # 代码块与行内代码里的 | ** 是内容本身（ASCII 图、正则、glob、SQL），不算残留。
    checked = 0
    offenders: list[tuple[int, str]] = []
    for idx, paragraph in enumerate(paragraphs[:sample]):
        if _is_code_paragraph(paragraph):
            continue            # 代码块的竖线/星号是内容本身（ASCII 图、正则、glob）
        checked += 1
        text, _code = _split_paragraph_text(paragraph)
        if "**" in text or "|" in text or "`" in text or "| --- |" in text:
            offenders.append((idx, text[:60]))
    LOG.info("抽样段落        : 前 %d 段中检查了 %d 段正文（跳过代码块）", sample, checked)
    assert not offenders, f"正文残留 Markdown 标记：{offenders[:3]}"

    leftovers: list[tuple[int, str]] = []
    glob_like = 0
    for idx, paragraph in enumerate(paragraphs):
        text, code = _split_paragraph_text(paragraph)
        if _is_code_paragraph(paragraph):
            if "**" in paragraph.text or "`" in paragraph.text:
                leftovers.append((idx, paragraph.text[:60]))
            continue
        if "**" in text or "`" in text or "~~~" in text or "| --- |" in text:
            leftovers.append((idx, text[:60]))
        elif "**" in code or "|" in code:
            glob_like += 1
    LOG.info("全篇残留扫描    : %d 处 Markdown 标记；另有 %d 段只在行内代码里出现 */|（glob 等原样保留）",
             len(leftovers), glob_like)
    assert not leftovers, f"全篇仍有 Markdown 标记：{leftovers[:3]}"

    sep_rows = 0
    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                if _DELIM_CELL_RE.match(cell.text.strip()) and "-" in cell.text:
                    sep_rows += 1
    LOG.info("表格分隔行残留  : %d", sep_rows)
    assert sep_rows == 0, "表格里出现了 Markdown 的 | --- | 分隔行"

    size_kb = path.stat().st_size / 1024
    LOG.info("文件大小        : %.1f KB (%d 字节)", size_kb, path.stat().st_size)
    LOG.info("绝对路径        : %s", path.resolve())
    LOG.info("断言全部通过：表格 > 0、Heading 1 > %d、正文无 ** | ` 残留、目录域与页码域就位",
             headings[1])


# ===================================================================== main
def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOG.handlers.clear()
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Markdown 毕业设计草稿 → 排版正确的 .docx")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    _configure_logging()
    if not args.input.is_file():
        LOG.error("找不到输入文件：%s", args.input)
        return 1

    md = args.input.read_text(encoding="utf-8")
    blocks = parse_blocks(md)

    title = ""
    for block in blocks:
        if block.kind == "heading" and block.level == 1:
            title = plain_text(block.text)
            break

    LOG.info("输入            : %s", args.input)
    LOG.info("文档标题        : %s", title)
    LOG.info("解析块数量      : %d（标题 %d / 段落 %d / 列表 %d / 引用 %d / 代码 %d / 表格 %d）",
             len(blocks),
             sum(1 for b in blocks if b.kind == "heading"),
             sum(1 for b in blocks if b.kind == "para"),
             sum(1 for b in blocks if b.kind == "list"),
             sum(1 for b in blocks if b.kind in ("quote", "quote_list")),
             sum(1 for b in blocks if b.kind == "code"),
             sum(1 for b in blocks if b.kind == "table"))

    doc = build_document(blocks, title)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(args.output))

    if COMPAT_NOTES:
        LOG.info("── Markdown 解析兼容处理（%d 条）", len(COMPAT_NOTES))
        for note in sorted(set(COMPAT_NOTES)):
            LOG.info("  * %s", note)

    verify(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
