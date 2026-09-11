"""解析器单元测试：用库内生成器构造真实文件，走真实解析路径。"""

from __future__ import annotations

import io

import pytest

from app.ingestion.parsers import get_parser
from app.ingestion.parsers.docx import DocxParser
from app.ingestion.parsers.pdf import PyMuPDFParser
from app.ingestion.parsers.pptx import PPTXParser
from app.ingestion.parsers.text import PlainTextParser


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


def _make_docx(paragraphs: list[str], table_rows: list[list[str]]) -> bytes:
    from docx import Document

    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    table = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
    for r, row in enumerate(table_rows):
        for c, cell in enumerate(row):
            table.rows[r].cells[c].text = cell
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pptx(slide_texts: list[str]) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    blank = prs.slide_layouts[6]
    for text in slide_texts:
        slide = prs.slides.add_slide(blank)
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        box.text_frame.text = text
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
    async def test_parse_paragraphs_and_table(self) -> None:
        data = _make_docx(
            ["导数定义", "泰勒展开"],
            [["考点", "频次"], ["极限", "高"]],
        )

        result = await DocxParser().parse_async("讲义.docx", data)

        assert "导数定义" in result.text
        assert "泰勒展开" in result.text
        assert "考点 | 频次" in result.text
        assert "极限 | 高" in result.text


class TestPPTXParser:
    async def test_parse_slides(self) -> None:
        data = _make_pptx(["第一章 行列式", "第二章 矩阵"])

        result = await PPTXParser().parse_async("课件.pptx", data)

        assert result.page_count == 2
        assert "第一章 行列式" in result.text
        assert "--- Slide 2 ---" in result.text


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
        assert isinstance(get_parser("a.pdf"), PyMuPDFParser)
        assert isinstance(get_parser("b.docx"), DocxParser)
        assert isinstance(get_parser("c.pptx"), PPTXParser)
        assert isinstance(get_parser("d.md"), PlainTextParser)


class TestParseAsync:
    async def test_sync_parser_runs_via_thread_pool(self) -> None:
        """parse_async 对同步解析器走线程池，结果一致。"""
        data = _make_pdf("thread pool", pages=1)
        result = await get_parser("x.pdf").parse_async("x.pdf", data)
        assert "thread pool" in result.text
