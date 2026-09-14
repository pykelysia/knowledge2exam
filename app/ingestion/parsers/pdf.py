"""PDF 解析器：文本层提取 + 页级质量体检 + 损坏页栅格化（供视觉 OCR 兜底）。"""

from __future__ import annotations

import pymupdf

from app.config import settings
from app.ingestion.parsers.base import ImageInfo, PageRender, Parser, ParseResult
from app.ingestion.quality import page_needs_ocr


class PyMuPDFParser(Parser):
    """PDF 解析器。

    逐页提取文本层并体检：健康页直接使用（零 LLM 成本）；乱码页（字体缺
    ToUnicode CMap）与无文本页（扫描件）按 `ocr_dpi` 渲染为 PNG 记入
    `page_renders`，由编排层调用视觉 LLM 转录后替换对应页文本。
    """

    def parse(self, filename: str, data: bytes) -> ParseResult:
        doc = pymupdf.open(stream=data, filetype="pdf")

        mode = getattr(settings, "pdf_ocr_mode", "auto")
        dpi = getattr(settings, "ocr_dpi", 200)
        max_ocr_pages = getattr(settings, "ocr_max_pages", 60)

        full_text_parts: list[str] = []
        page_renders: list[PageRender] = []
        renders_truncated = False
        images: list[ImageInfo] = []
        page_count = len(doc)

        for page_num in range(page_count):
            page = doc[page_num]
            text = page.get_text()
            full_text_parts.append(f"--- Page {page_num + 1} ---\n{text}")

            page_images = self._extract_page_images(doc, page, page_num)
            images.extend(page_images)

            if mode == "off":
                continue
            need, reason = (True, "forced") if mode == "always" else page_needs_ocr(text)
            if not need:
                continue
            if len(page_renders) >= max_ocr_pages:
                renders_truncated = True
                continue
            png = self._render_page_png(page, dpi)
            if png is not None:
                page_renders.append(PageRender(page=page_num + 1, data=png, reason=reason))

        doc.close()

        text = "\n\n".join(full_text_parts)
        return ParseResult(
            char_count=len(text),
            page_count=page_count,
            text=text,
            images=images or None,
            page_renders=page_renders or None,
            page_renders_truncated=renders_truncated,
        )

    @staticmethod
    def _render_page_png(page: pymupdf.Page, dpi: int) -> bytes | None:
        """把页面渲染为 PNG；单页渲染失败不影响整体。"""
        try:
            pix = page.get_pixmap(dpi=dpi)
            return pix.tobytes("png")
        except Exception:
            return None

    @staticmethod
    def _extract_page_images(doc, page, page_num: int) -> list[ImageInfo]:
        """提取单页中的内嵌图片。"""
        result: list[ImageInfo] = []
        try:
            img_list = page.get_images()
        except Exception:
            return result

        for img_item in img_list:
            xref = img_item[0]
            try:
                base_image = doc.extract_image(xref)
                if base_image is None:
                    continue
                image_bytes = base_image.get("image")
                if image_bytes is None:
                    continue

                # 尝试获取图片在页面中的位置
                bbox = None
                try:
                    rect = page.get_image_rects(img_item[0])
                    if rect:
                        bbox = (rect[0].x0, rect[0].y0, rect[0].x1, rect[0].y1)
                except Exception:
                    pass

                result.append(
                    ImageInfo(
                        page=page_num + 1,
                        data=image_bytes,
                        bbox=bbox,
                        order=len(result),
                    )
                )
            except Exception:
                # 单张图片提取失败不影响整体
                continue

        return result
