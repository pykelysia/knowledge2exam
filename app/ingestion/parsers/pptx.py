"""PPTX 解析器：使用 python-pptx 提取文本和内嵌图片。"""

from __future__ import annotations

import io
from typing import Any

from app.ingestion.parsers.base import ImageInfo, Parser, ParseResult


def _collect_slide_content(
    shapes: Any,
    texts: list[str],
    images: list[ImageInfo],
    page: int,
) -> None:
    """递归提取一组 shape 的文本（含组合形状）、表格行与图片。"""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            _collect_slide_content(shape.shapes, texts, images, page)
            continue
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                text = para.text.strip()
                if text:
                    texts.append(text)
        # 提取内嵌图片
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            try:
                image_bytes = shape.image.blob
                images.append(
                    ImageInfo(
                        page=page,
                        data=image_bytes,
                        order=len(images),
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        # 提取表格内容
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                row_text = " | ".join(
                    cell.text.strip() for cell in row.cells if cell.text.strip()
                )
                if row_text:
                    texts.append(row_text)


class PPTXParser(Parser):
    """PPTX 解析器，提取每页幻灯片文本和内嵌图片。"""

    def parse(self, filename: str, data: bytes) -> ParseResult:
        try:
            from pptx import Presentation
        except ImportError as exc:
            raise RuntimeError("python-pptx 未安装，请运行 `pip install python-pptx`") from exc

        prs = Presentation(io.BytesIO(data))

        # 逐页提取文本
        parts: list[str] = []
        page_count = len(prs.slides)
        images: list[ImageInfo] = []

        for slide_num, slide in enumerate(prs.slides, start=1):
            slide_texts: list[str] = []
            _collect_slide_content(slide.shapes, slide_texts, images, slide_num)
            if slide_texts:
                parts.append(f"--- Slide {slide_num} ---\n" + "\n".join(slide_texts))

        text = "\n\n".join(parts)

        return ParseResult(
            char_count=len(text),
            page_count=page_count,
            text=text,
            images=images if images else None,
        )
