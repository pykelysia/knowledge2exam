"""PDF 类型检测与版面分析测试（合成 PDF 走真实解析路径，不触 LLM）。"""

from __future__ import annotations

import io
import math

import pymupdf
from PIL import Image

from app.config import settings
from app.ingestion.image_enhance import enhance_scan_image
from app.ingestion.parsers.pdf import PyMuPDFParser
from app.ingestion.pdf_layout import (
    PageStats,
    PdfType,
    classify_document,
    collect_page_contents,
    skip_embedded_images,
)


def _stats(
    page: int,
    *,
    chars: int = 500,
    reason: str = "",
    coverage: float = 0.0,
    tables: bool = False,
    charts: bool = False,
) -> PageStats:
    return PageStats(
        page=page,
        text_chars=chars,
        ocr_reason=reason,
        image_coverage=coverage,
        has_tables=tables,
        has_charts=charts,
    )


def _noise_png(width: int = 400, height: int = 300) -> bytes:
    """带灰度噪声的 PNG（避免被当成纯色装饰图）。"""
    buf = io.BytesIO()
    Image.effect_noise((width, height), 40).save(buf, format="PNG")
    return buf.getvalue()


def _make_scan_pdf(pages: int = 2) -> bytes:
    """整页图片、无文本层的合成扫描件。"""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(page.rect, stream=_noise_png())
    data = doc.tobytes()
    doc.close()
    return data


def _make_mixed_pdf() -> bytes:
    """文本 + 大幅图片（覆盖率超过显著阈值）的合成混合型。"""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "mixed content page")
    # A4 约 595x842pt，图片约 428x400 → 覆盖率 ~0.4
    page.insert_image(pymupdf.Rect(72, 300, 500, 700), stream=_noise_png())
    data = doc.tobytes()
    doc.close()
    return data


def _make_table_pdf() -> bytes:
    """带完整网格线表格的合成 PDF（find_tables 可检出）。"""
    doc = pymupdf.open()
    page = doc.new_page()
    x0, y0, col_w, row_h = 100.0, 100.0, 120.0, 30.0
    for i in range(3):  # 2 列 3 行 → 3 条竖线 / 3 条横线
        x = x0 + i * col_w
        page.draw_line(pymupdf.Point(x, y0), pymupdf.Point(x, y0 + 2 * row_h))
    for j in range(3):
        y = y0 + j * row_h
        page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x0 + 2 * col_w, y))
    cells = [["A1", "B1"], ["A2", "B2"]]
    for r, row in enumerate(cells):
        for c, cell in enumerate(row):
            page.insert_text((x0 + c * col_w + 5, y0 + r * row_h + 20), cell)
    data = doc.tobytes()
    doc.close()
    return data


class TestClassifyDocument:
    def test_pure_text(self) -> None:
        pages = [_stats(1), _stats(2), _stats(3)]
        assert classify_document(pages) is PdfType.PURE_TEXT

    def test_garbled_page_keeps_pure_text(self) -> None:
        # 乱码页无视觉元素，由页级体检兜底，不影响文档级类型
        pages = [_stats(1), _stats(2, reason="garbled"), _stats(3)]
        assert classify_document(pages) is PdfType.PURE_TEXT

    def test_scanned(self) -> None:
        pages = [_stats(i, reason="no_text", coverage=0.95) for i in range(1, 5)]
        assert classify_document(pages) is PdfType.SCANNED

    def test_scanned_with_one_text_page(self) -> None:
        pages = [_stats(i, reason="no_text", coverage=0.95) for i in range(1, 10)]
        pages.append(_stats(10))
        assert classify_document(pages) is PdfType.SCANNED

    def test_half_scanned_is_mixed(self) -> None:
        pages = [_stats(1, reason="no_text", coverage=0.95), _stats(2)]
        assert classify_document(pages) is PdfType.MIXED

    def test_mixed_with_image(self) -> None:
        pages = [_stats(1, coverage=0.4), _stats(2)]
        assert classify_document(pages) is PdfType.MIXED

    def test_mixed_with_table(self) -> None:
        pages = [_stats(1, tables=True), _stats(2)]
        assert classify_document(pages) is PdfType.MIXED

    def test_small_coverage_is_not_visual(self) -> None:
        pages = [_stats(1, coverage=0.05), _stats(2)]
        assert classify_document(pages) is PdfType.PURE_TEXT

    def test_empty(self) -> None:
        assert classify_document([]) is PdfType.PURE_TEXT


class TestSkipEmbeddedImages:
    def test_scanned_page_skipped(self) -> None:
        stats = _stats(1, reason="no_text", coverage=0.9)
        assert skip_embedded_images(stats) is True

    def test_hidden_text_layer_page_skipped(self) -> None:
        stats = _stats(1, chars=800, coverage=0.9)
        assert skip_embedded_images(stats) is True

    def test_healthy_page_with_figure_kept(self) -> None:
        # 大图但文本少：可能是图表页，图片需要 OCR，不能跳过
        stats = _stats(1, chars=30, coverage=0.9)
        assert skip_embedded_images(stats) is False

    def test_normal_text_page_kept(self) -> None:
        assert skip_embedded_images(_stats(1)) is False


def _gray_std(img: Image.Image) -> float:
    """灰度直方图标准差（与 postprocess 预筛选同一算法）。"""
    hist = img.convert("L").histogram()
    total = sum(hist)
    mean = sum(i * v for i, v in enumerate(hist)) / total
    return math.sqrt(sum(v * (i - mean) ** 2 for i, v in enumerate(hist)) / total)


class TestEnhanceScanImage:
    def test_low_contrast_boosted(self) -> None:
        # 100~125 的低对比渐变 → autocontrast 后应拉开到全范围
        buf = io.BytesIO()
        Image.linear_gradient("L").point(lambda x: 100 + x // 10).save(buf, format="PNG")
        src_std = _gray_std(Image.open(buf))

        enhanced = enhance_scan_image(buf.getvalue())
        out = Image.open(io.BytesIO(enhanced))
        assert out.format == "PNG"
        assert _gray_std(out) > src_std

    def test_invalid_input_returns_original(self) -> None:
        assert enhance_scan_image(b"not a png") == b"not a png"


class TestParserRouting:
    async def test_scan_pdf_routes_to_scanned(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "pdf_ocr_mode", "auto")
        monkeypatch.setattr(settings, "ocr_dpi", 72)

        result = await PyMuPDFParser().parse_async("scan.pdf", _make_scan_pdf(pages=2))

        assert result.pdf_type == "scanned"
        assert result.page_renders is not None
        assert [r.page for r in result.page_renders] == [1, 2]
        assert all(r.reason == "no_text" for r in result.page_renders)
        # 扫描页跳过内嵌图片提取（内容由整页 OCR 承载）
        assert result.images is None
        assert "--- Page 1 ---" in result.text

    async def test_text_pdf_routes_to_pure_text(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "pdf_ocr_mode", "auto")
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "plain text page")
        data = doc.tobytes()
        doc.close()

        result = await PyMuPDFParser().parse_async("a.pdf", data)

        assert result.pdf_type == "pure_text"
        assert result.page_renders is None
        assert "plain text page" in result.text

    async def test_mixed_pdf_extracts_image_with_marker(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "pdf_ocr_mode", "auto")

        result = await PyMuPDFParser().parse_async("mixed.pdf", _make_mixed_pdf())

        assert result.pdf_type == "mixed"
        assert result.images is not None
        assert len(result.images) == 1
        assert result.images[0].marker == "[图: p1-1]"
        assert result.images[0].kind == "image"
        assert "[图: p1-1]" in result.text
        assert "mixed content page" in result.text

    async def test_table_pdf_renders_markdown_table(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "pdf_ocr_mode", "auto")

        result = await PyMuPDFParser().parse_async("table.pdf", _make_table_pdf())

        assert result.pdf_type == "mixed"
        assert "|" in result.text
        for cell in ("A1", "B1", "A2", "B2"):
            assert cell in result.text

    async def test_mixed_doc_healthy_pages_not_rendered(self, monkeypatch) -> None:
        # 混合型但页健康：区域组装足够，无需整页 OCR
        monkeypatch.setattr(settings, "pdf_ocr_mode", "auto")
        result = await PyMuPDFParser().parse_async("mixed.pdf", _make_mixed_pdf())
        assert result.page_renders is None


class TestCollectPageContents:
    async def test_structure_preserved_on_text_pdf(self, monkeypatch) -> None:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Chapter One", fontsize=20)
        page.insert_text((72, 140), "body text " * 6, fontsize=11)
        data = doc.tobytes()
        doc.close()

        # 无 TOC：字体大小启发式应把大字号短行识别为标题
        result = await PyMuPDFParser().parse_async("a.pdf", data)
        assert "# Chapter One" in result.text

    async def test_toc_overrides_heuristic(self, monkeypatch) -> None:
        doc = pymupdf.open()
        page = doc.new_page()
        # 字号不大，但 TOC 声明它是 1 级标题
        page.insert_text((72, 100), "Section Title", fontsize=11)
        page.insert_text((72, 140), "body text " * 6, fontsize=11)
        doc.set_toc([[1, "Section Title", 1]])
        data = doc.tobytes()
        doc.close()

        result = await PyMuPDFParser().parse_async("a.pdf", data)
        assert "# Section Title" in result.text

    async def test_collect_stats_single_pass(self, monkeypatch) -> None:
        doc = pymupdf.open(stream=_make_mixed_pdf(), filetype="pdf")
        contents = collect_page_contents(doc)
        doc.close()

        assert len(contents) == 1
        stats = contents[0].stats
        assert stats.healthy is True
        assert stats.image_coverage > 0.15
        assert any("mixed content page" in b.text for b in contents[0].blocks)
        assert len(contents[0].images) == 1
