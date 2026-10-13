"""解析器单元测试：用库内生成器构造真实文件，走真实解析路径。"""

from __future__ import annotations

import io

import pytest

from app.ingestion.parsers import get_parser
from app.ingestion.parsers.docx import DocxParser
from app.ingestion.parsers.image import ImageParser
from app.ingestion.parsers.pdf import PyMuPDFParser
from app.ingestion.parsers.pptx import PPTXParser
from app.ingestion.parsers.text import PlainTextParser


def _png_bytes(
    size: tuple[int, int] = (80, 60),
    color: tuple[int, int, int] = (200, 30, 30),
) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_pdf(text: str, pages: int = 2) -> bytes:
    """默认字体不含 CJK 字形，PDF 夹具统一用 ASCII 文本。"""
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"{text} 第{i + 1}页")
    data = doc.tobytes()
    doc.close()
    return data


def _make_docx(
    paragraphs: list[str],
    table_rows: list[list[str]],
    *,
    heading: tuple[int, str] | None = None,
    image: bytes | None = None,
) -> bytes:
    from docx import Document

    doc = Document()
    if heading is not None:
        level, text = heading
        doc.add_heading(text, level=level)
    for p in paragraphs:
        doc.add_paragraph(p)
    if image is not None:
        doc.add_picture(io.BytesIO(image))
    if table_rows:
        table = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for r, row in enumerate(table_rows):
            for c, cell in enumerate(row):
                table.rows[r].cells[c].text = cell
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pptx(slides: list[dict]) -> bytes:
    """构造 PPTX。slides 项支持 title/texts/table/pictures/background/notes。"""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    layout = prs.slide_layouts[5]  # Title Only：带标题占位符
    for spec in slides:
        slide = prs.slides.add_slide(layout)
        if spec.get("title") is not None:
            slide.shapes.title.text = spec["title"]
        for text in spec.get("texts", []):
            box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(4), Inches(1))
            box.text_frame.text = text
        table_rows = spec.get("table")
        if table_rows:
            shape = slide.shapes.add_table(
                len(table_rows), len(table_rows[0]), Inches(1), Inches(4), Inches(4), Inches(1)
            )
            for r, row in enumerate(table_rows):
                for c, cell in enumerate(row):
                    shape.table.rows[r].cells[c].text = cell
        for png in spec.get("pictures", []):
            slide.shapes.add_picture(io.BytesIO(png), Inches(1), Inches(5), Inches(2), Inches(1.5))
        if spec.get("background"):
            slide.shapes.add_picture(
                io.BytesIO(spec["background"]), 0, 0,
                width=prs.slide_width, height=prs.slide_height,
            )
        if spec.get("notes"):
            slide.notes_slide.notes_text_frame.text = spec["notes"]
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


class TestPyMuPDFParser:
    async def test_parse_pdf_pages_and_text(self) -> None:
        data = _make_pdf("vector algebra", pages=2)

        result = await PyMuPDFParser().parse_async("向量.pdf", data)

        assert result.page_count == 2
        assert "vector algebra" in result.text
        assert "--- Page 1 ---" in result.text
        assert "--- Page 2 ---" in result.text
        assert result.char_count == len(result.text)


class TestDocxParser:
    async def test_parse_heading_paragraphs_and_table(self) -> None:
        data = _make_docx(
            ["导数定义", "泰勒展开"],
            [["考点", "频次"], ["极限", "高"]],
            heading=(1, "第二章 极限"),
        )

        result = await DocxParser().parse_async("讲义.docx", data)

        # 标题样式 → # 层级标题行
        assert "# 第二章 极限" in result.text
        assert "导数定义" in result.text
        assert "泰勒展开" in result.text
        # 表格转 Markdown：表头 + 分隔行 + 数据行
        assert "| 考点 | 频次 |" in result.text
        assert "| 极限 | 高 |" in result.text
        assert "--- | ---" in result.text
        # DOCX 无页概念
        assert result.page_count is None
        assert result.images is None

    async def test_image_placeholder_and_marker(self) -> None:
        png = _png_bytes()
        data = _make_docx(["正文段落"], [], image=png)

        result = await DocxParser().parse_async("讲义.docx", data)

        assert "[图: docx-1]" in result.text
        assert result.images is not None
        assert result.images[0].marker == "[图: docx-1]"
        assert result.images[0].data == png


class TestPPTXParser:
    async def test_parse_slides_title_body_notes(self) -> None:
        data = _make_pptx([
            {"title": "动量定理", "texts": ["Ft = mv₂ − mv₁"], "notes": "考试常考变力冲量"},
            {"title": "动量守恒定律", "texts": ["系统总动量保持不变"]},
        ])

        result = await PPTXParser().parse_async("课件.pptx", data)

        assert result.page_count == 2
        # 标题占位符 → # 一级标题，位于该页块首
        assert result.text.index("# 动量定理") < result.text.index("Ft = mv₂ − mv₁")
        assert "# 动量守恒定律" in result.text
        # 备注作为该页附加段落
        assert "备注：考试常考变力冲量" in result.text
        # 不再输出 Slide 标记行
        assert "--- Slide" not in result.text

    async def test_table_to_markdown(self) -> None:
        data = _make_pptx([
            {"title": "物理量", "table": [["符号", "单位"], ["I", "N·s"]]},
        ])

        result = await PPTXParser().parse_async("课件.pptx", data)

        assert "| 符号 | 单位 |" in result.text
        assert "| I | N·s |" in result.text

    async def test_content_picture_placeholder(self) -> None:
        data = _make_pptx([{"title": "示意图", "pictures": [_png_bytes()]}])

        result = await PPTXParser().parse_async("课件.pptx", data)

        assert "[图: slide1-1]" in result.text
        assert result.images is not None
        assert result.images[0].marker == "[图: slide1-1]"

    async def test_repeated_picture_treated_as_logo(self) -> None:
        logo = _png_bytes()
        data = _make_pptx([
            {"title": "第一页", "texts": ["正文一"], "pictures": [logo]},
            {"title": "第二页", "texts": ["正文二"], "pictures": [logo]},
        ])

        result = await PPTXParser().parse_async("课件.pptx", data)

        # 跨页同字节图片（logo/水印）整体跳过
        assert result.images is None
        assert "[图:" not in result.text

    async def test_full_slide_picture_treated_as_background(self) -> None:
        data = _make_pptx([
            {"title": "封面", "texts": ["正文"], "background": _png_bytes(size=(120, 90))},
        ])

        result = await PPTXParser().parse_async("课件.pptx", data)

        assert result.images is None
        assert "[图:" not in result.text

    def test_placeholder_kind(self) -> None:
        from pptx.enum.shapes import PP_PLACEHOLDER

        from app.ingestion.parsers.pptx import _placeholder_kind

        class _Fmt:
            def __init__(self, kind: object) -> None:
                self.type = kind

        class _Shape:
            def __init__(self, kind: object, is_placeholder: bool = True) -> None:
                self.placeholder_format = _Fmt(kind)
                self.is_placeholder = is_placeholder

        assert _placeholder_kind(_Shape(PP_PLACEHOLDER.TITLE)) == "title"
        assert _placeholder_kind(_Shape(PP_PLACEHOLDER.CENTER_TITLE)) == "title"
        assert _placeholder_kind(_Shape(PP_PLACEHOLDER.SLIDE_NUMBER)) == "ignore"
        assert _placeholder_kind(_Shape(PP_PLACEHOLDER.DATE)) == "ignore"
        assert _placeholder_kind(_Shape(PP_PLACEHOLDER.FOOTER)) == "ignore"
        assert _placeholder_kind(_Shape(PP_PLACEHOLDER.BODY)) == "body"
        assert _placeholder_kind(_Shape(PP_PLACEHOLDER.TITLE, is_placeholder=False)) == "body"


class TestPlainTextParser:
    async def test_utf8(self) -> None:
        result = await PlainTextParser().parse_async("a.md", "# 标题\n正文".encode())
        assert result.text == "# 标题\n正文"

    async def test_latin1_fallback(self) -> None:
        payload = b"caf\xe9"  # 非 UTF-8
        result = await PlainTextParser().parse_async("a.txt", payload)
        assert result.text == "café"


class TestGetParser:
    def test_unsupported_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported"):
            get_parser("legacy.doc")

    def test_supported_extensions_resolve(self) -> None:
        # 各格式直连原生解析器，不再经 PDF 规范化
        assert isinstance(get_parser("a.pdf"), PyMuPDFParser)
        assert isinstance(get_parser("b.docx"), DocxParser)
        assert isinstance(get_parser("c.pptx"), PPTXParser)
        assert isinstance(get_parser("e.png"), ImageParser)
        assert isinstance(get_parser("d.md"), PlainTextParser)


class TestParseAsync:
    async def test_sync_parser_runs_via_thread_pool(self) -> None:
        """parse_async 对同步解析器走线程池，结果一致。"""
        data = _make_pdf("thread pool", pages=1)
        result = await get_parser("x.pdf").parse_async("x.pdf", data)
        assert "thread pool" in result.text
