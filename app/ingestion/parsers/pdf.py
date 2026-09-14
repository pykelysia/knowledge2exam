"""PDF 解析器：类型检测 + 三路处理（docs/pdf_analyzer.md）。

处理路由由文档级类型（classify_document）决定：
- pure_text：文本层结构化抽取（TOC/字号标题、段落块、表格 Markdown、图片占位符）；
- scanned：全部页栅格化（图像增强后）交视觉 LLM 整页转录，阅读顺序由转录承担；
- mixed：区域切分（文本区/表格区/图表区/图片区），表格转 Markdown，图表/图片
  经视觉 OCR 后由编排层按占位符回填原文位置（上下文融合）。

`pdf_ocr_mode` 语义保持不变：off 禁用 OCR；always 全页强制渲染；
auto 按文档类型 + 页级体检路由。
"""

from __future__ import annotations

import logging

import pymupdf

from app.config import settings
from app.ingestion.image_enhance import enhance_scan_image
from app.ingestion.parsers.base import ImageInfo, PageRender, Parser, ParseResult
from app.ingestion.pdf_layout import (
    HeadingMap,
    PageContent,
    PageStats,
    PdfType,
    build_heading_map,
    classify_document,
    collect_page_contents,
    is_region_covered,
    skip_embedded_images,
)

logger = logging.getLogger(__name__)


class PyMuPDFParser(Parser):
    """PDF 解析器：类型检测 + 结构化抽取 + 扫描页栅格化兜底。"""

    def parse(self, filename: str, data: bytes) -> ParseResult:
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            contents = collect_page_contents(doc)
            pdf_type = classify_document([content.stats for content in contents])
            headings = build_heading_map(doc, contents)

            mode = getattr(settings, "pdf_ocr_mode", "auto")
            dpi = getattr(settings, "ocr_dpi", 200)
            max_ocr_pages = getattr(settings, "ocr_max_pages", 60)
            enhance = getattr(settings, "pdf_scan_enhance", True)

            text_parts: list[str] = []
            images: list[ImageInfo] = []
            page_renders: list[PageRender] = []
            renders_truncated = False
            image_seq = 0
            chart_seq = 0

            for content in contents:
                page = doc[content.stats.page - 1]
                body, page_images, image_seq, chart_seq = self._assemble_page(
                    page, content, headings, image_seq, chart_seq
                )
                images.extend(page_images)
                text_parts.append(f"--- Page {content.stats.page} ---\n{body}")

                if mode == "off":
                    continue
                if mode == "always":
                    need, reason = True, "forced"
                else:
                    need, reason = self._needs_render(pdf_type, content.stats)
                if not need:
                    continue
                if len(page_renders) >= max_ocr_pages:
                    renders_truncated = True
                    continue
                png = self._render_page_png(page, dpi)
                if png is None:
                    continue
                if enhance:
                    png = enhance_scan_image(png)
                page_renders.append(
                    PageRender(page=content.stats.page, data=png, reason=reason)
                )

            text = "\n\n".join(text_parts)
            return ParseResult(
                char_count=len(text),
                page_count=len(contents),
                text=text,
                images=images or None,
                page_renders=page_renders or None,
                page_renders_truncated=renders_truncated,
                pdf_type=pdf_type.value,
            )
        finally:
            doc.close()

    # ---- 区域组装 ----

    def _assemble_page(
        self,
        page: pymupdf.Page,
        content: PageContent,
        headings: HeadingMap,
        image_seq: int,
        chart_seq: int,
    ) -> tuple[str, list[ImageInfo], int, int]:
        """把单页各区域按阅读顺序（y0, x0）组装为 Markdown 正文。

        返回 (正文, 图片/图表信息列表, 更新后的图片序号, 更新后的图表序号)。
        """
        page_no = content.stats.page
        items: list[tuple[float, float, str]] = []
        covered_regions = [t.bbox for t in content.tables] + [c.bbox for c in content.charts]

        # 文本区：被表格/图表覆盖的块剔除（内容由表格 Markdown / 图表转录承载）
        for block in content.blocks:
            if is_region_covered(block.bbox, covered_regions):
                continue
            text = block.text
            level = headings.level_for(page_no, block.bbox[1])
            if level:
                text = f"{'#' * level} {text}"
            items.append((block.bbox[1], block.bbox[0], text))

        # TOC 中未命中文本块的标题条目（无目标坐标的按页首插入）
        for y, level, title in headings.take_pending(page_no):
            heading = f"{'#' * level} {title}".strip()
            items.append((y if y is not None else -1.0, 0.0, heading))

        # 表格区：直接转 Markdown
        for table in content.tables:
            items.append((table.bbox[1], table.bbox[0], table.markdown))

        # 图片区 / 图表区：登记占位符，OCR 文本由编排层回填
        page_images: list[ImageInfo] = []
        if not skip_embedded_images(content.stats):
            for ref in content.images:
                image_seq += 1
                marker = f"[图: p{page_no}-{image_seq}]"
                page_images.append(
                    ImageInfo(
                        page=page_no,
                        data=ref.data,
                        bbox=ref.bbox,
                        order=image_seq,
                        marker=marker,
                    )
                )
                items.append((ref.bbox[1], ref.bbox[0], marker))
            for ref in content.charts:
                png = self._render_region_png(page, ref.bbox)
                if png is None:
                    continue
                chart_seq += 1
                marker = f"[图表: p{page_no}-{chart_seq}]"
                page_images.append(
                    ImageInfo(
                        page=page_no,
                        data=png,
                        bbox=ref.bbox,
                        order=image_seq + chart_seq,
                        kind="chart",
                        marker=marker,
                    )
                )
                items.append((ref.bbox[1], ref.bbox[0], marker))

        items.sort(key=lambda item: (item[0], item[1]))
        body = "\n\n".join(text for _, _, text in items if text.strip())
        return body, page_images, image_seq, chart_seq

    # ---- 渲染与路由 ----

    @staticmethod
    def _needs_render(pdf_type: PdfType, stats: PageStats) -> tuple[bool, str]:
        """auto 模式的渲染判定：扫描件全页渲染，其余按页级体检。"""
        if pdf_type is PdfType.SCANNED:
            return True, stats.ocr_reason or "forced"
        if stats.ocr_reason:
            return True, stats.ocr_reason
        return False, ""

    @staticmethod
    def _render_page_png(page: pymupdf.Page, dpi: int) -> bytes | None:
        """把页面渲染为 PNG；单页渲染失败不影响整体。"""
        try:
            pix = page.get_pixmap(dpi=dpi)
            return pix.tobytes("png")
        except Exception:
            return None

    @staticmethod
    def _render_region_png(
        page: pymupdf.Page, bbox: tuple[float, float, float, float]
    ) -> bytes | None:
        """按区域 bbox 渲染 PNG（图表区送视觉 LLM 描述）。"""
        try:
            pix = page.get_pixmap(clip=pymupdf.Rect(*bbox), dpi=getattr(settings, "ocr_dpi", 200))
            return pix.tobytes("png")
        except Exception:
            return None
