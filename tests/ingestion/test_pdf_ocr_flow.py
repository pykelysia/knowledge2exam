"""PDF 页级体检 + 栅格化 + OCR 替换流程测试（OCR 用 monkeypatch，不触真实 LLM）。"""

from __future__ import annotations

import io

import pytest

from app.config import settings
from app.ingestion.convert import soffice_available
from app.ingestion.ocr import VisionLLMOCR
from app.ingestion.parsers import get_parser
from app.ingestion.parsers.pdf import PyMuPDFParser
from app.ingestion.postprocess import replace_page_text, run_page_ocr
from tests.ingestion.test_parsers import _make_docx, _make_pdf
from tests.ingestion.test_pdf_layout import _make_scan_pdf


@pytest.fixture
def force_ocr_mode(monkeypatch):
    monkeypatch.setattr(settings, "pdf_ocr_mode", "always")
    monkeypatch.setattr(settings, "ocr_dpi", 72)
    monkeypatch.setattr(settings, "ocr_max_pages", 60)


class TestParserPageRenders:
    async def test_always_mode_renders_every_page(self, force_ocr_mode) -> None:
        result = await PyMuPDFParser().parse_async("a.pdf", _make_pdf("content", pages=3))

        assert result.page_renders is not None
        assert [r.page for r in result.page_renders] == [1, 2, 3]
        assert all(r.reason == "forced" for r in result.page_renders)
        assert all(r.data[:8] == b"\x89PNG\r\n\x1a\n" for r in result.page_renders)
        assert result.page_renders_truncated is False
        # 页文本结构保留，待编排层替换
        assert "--- Page 1 ---" in result.text

    async def test_auto_mode_clean_text_no_renders(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "pdf_ocr_mode", "auto")
        result = await PyMuPDFParser().parse_async("a.pdf", _make_pdf("clean text", pages=2))
        assert result.page_renders is None

    async def test_off_mode_never_renders(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "pdf_ocr_mode", "off")
        result = await PyMuPDFParser().parse_async("a.pdf", _make_pdf("text", pages=2))
        assert result.page_renders is None

    async def test_max_pages_truncates(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "pdf_ocr_mode", "always")
        monkeypatch.setattr(settings, "ocr_dpi", 72)
        monkeypatch.setattr(settings, "ocr_max_pages", 2)
        result = await PyMuPDFParser().parse_async("a.pdf", _make_pdf("content", pages=4))

        assert result.page_renders is not None
        assert [r.page for r in result.page_renders] == [1, 2]
        assert result.page_renders_truncated is True


class _FakeOCR:
    """前 fail_first_n 次调用抛错，之后返回可区分的 markdown。"""

    def __init__(self, fail_first_n: int) -> None:
        self.remaining_failures = fail_first_n

    async def extract_markdown(self, image_bytes: bytes) -> str:
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise RuntimeError("boom")
        return f"OCR转录-{len(image_bytes)}B"


class TestRunPageOcrAndReplace:
    async def test_partial_failure_and_page_replacement(
        self, monkeypatch, force_ocr_mode
    ) -> None:
        result = await PyMuPDFParser().parse_async("a.pdf", _make_pdf("original", pages=3))
        assert result.page_renders is not None

        fake = _FakeOCR(fail_first_n=1)
        monkeypatch.setattr(VisionLLMOCR, "from_settings", classmethod(lambda cls: fake))

        # concurrency=1 保证失败落在第 1 页，断言确定性
        succeeded, failed = await run_page_ocr(
            result.page_renders, concurrency=1, timeout_seconds=5
        )

        assert [r.page for r in failed] == [1]
        assert set(succeeded) == {2, 3}

        merged = replace_page_text(result.text, succeeded)
        assert "OCR转录-" in merged
        assert "--- Page 1 ---" in merged  # 失败页保留原文
        assert "--- Page 2 ---" in merged
        assert "--- Page 3 ---" in merged


class TestScannedDocFlow:
    async def test_scan_pdf_all_pages_replaced_by_ocr(self, monkeypatch) -> None:
        """扫描件（整页图片无文本层）在 auto 模式下全页渲染并被 OCR 替换。"""
        monkeypatch.setattr(settings, "pdf_ocr_mode", "auto")
        monkeypatch.setattr(settings, "ocr_dpi", 72)

        result = await PyMuPDFParser().parse_async("scan.pdf", _make_scan_pdf(pages=2))
        assert result.pdf_type == "scanned"
        assert result.page_renders is not None
        assert len(result.page_renders) == 2

        monkeypatch.setattr(
            VisionLLMOCR, "from_settings", classmethod(lambda cls: _FakeOCR(fail_first_n=0))
        )
        succeeded, failed = await run_page_ocr(result.page_renders)
        assert not failed
        assert set(succeeded) == {1, 2}

        merged = replace_page_text(result.text, succeeded)
        assert "OCR转录-" in merged
        assert merged.count("--- Page") == 2
        assert merged.count("OCR转录-") == 2

    async def test_scan_pdf_render_is_enhanced_png(self, monkeypatch) -> None:
        """默认开启图像增强时，渲染图仍是合法 PNG。"""
        monkeypatch.setattr(settings, "pdf_ocr_mode", "auto")
        monkeypatch.setattr(settings, "ocr_dpi", 72)

        result = await PyMuPDFParser().parse_async("scan.pdf", _make_scan_pdf(pages=1))
        assert result.page_renders is not None
        assert result.page_renders[0].data[:8] == b"\x89PNG\r\n\x1a\n"


class TestConvertedToPdfParser:
    async def test_image_wrapped_into_single_page_pdf(self, force_ocr_mode) -> None:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (600, 800), (250, 250, 250)).save(buf, format="PNG")
        parser = get_parser("photo.png")

        result = await parser.parse_async("photo.png", buf.getvalue())

        assert result.page_count == 1
        assert result.page_renders is not None  # 图片无文本层 → 整页走视觉 OCR

    async def test_docx_falls_back_to_native_without_soffice(self, monkeypatch) -> None:
        import shutil as shutil_module

        from app.ingestion.convert import soffice_available

        monkeypatch.setattr(shutil_module, "which", lambda name: None)
        assert soffice_available() is False

        parser = get_parser("讲义.docx")
        result = await parser.parse_async(
            "讲义.docx", _make_docx(["导数定义", "泰勒展开"], [["考点", "频次"]])
        )

        # soffice 缺失 → 回落原生 DocxParser，解析照常成功
        assert "导数定义" in result.text
        assert result.page_renders is None

    @pytest.mark.skipif(not soffice_available(), reason="LibreOffice 未安装")
    async def test_docx_converts_to_pdf(self) -> None:
        parser = get_parser("讲义.docx")
        result = await parser.parse_async(
            "讲义.docx", _make_docx(["导数定义"], [["考点", "频次"]])
        )

        assert result.page_count >= 1
        assert "导数定义" in result.text  # LibreOffice 产出的 PDF 文本层完好
