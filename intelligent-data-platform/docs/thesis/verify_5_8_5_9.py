"""核对 5.8/5.9 已进入论文 DOCX，并抽查关键证据串。

用法：python docs/thesis/verify_5_8_5_9.py
"""
from pathlib import Path

from docx import Document

DOCX = Path(__file__).parent / "毕业论文材料_蒋树阳" / "01_毕业论文正文.docx"
KEYS = [
    "任务知识（Skill）的边界",
    "事实一：本次部署的实际路径是直连 HTTP",
    "事实二：MCP 作为可选实现已经部署并可逐一验证",
    "事实三：已做过的对照是工具级结果对照",
    "TABLE_NOT_ALLOWED",
    "17068016.12",
]


def main() -> int:
    doc = Document(DOCX)
    paras = [p.text for p in doc.paragraphs]
    for prefix in ("5.8 ", "5.9 ", "5.10 "):
        hits = [i for i, text in enumerate(paras) if text.startswith(prefix)]
        print(prefix.strip(), hits)
        assert hits, f"缺少章节：{prefix}"
    for key in KEYS:
        hits = [i for i, text in enumerate(paras) if key in text]
        print(repr(key), hits[:5])
        assert hits, f"缺少关键串：{key}"
    print("OK", DOCX)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
