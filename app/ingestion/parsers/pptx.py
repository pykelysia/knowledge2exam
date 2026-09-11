"""PPTX 解析器：使用 python-pptx 提取文本和内嵌图片。"""

from __future__ import annotations

import io

from app.ingestion.parsers.base import ImageInfo, Parser, ParseResult


class PPTXParser(Parser):
    """PPTX 解析器，提取每页幻灯片文本和内嵌图片。"""

    def parse(self, filename: str, data: bytes) -> ParseResult:
        try:
            from pptx import Presentation
            from pptx.enum.shapes import MSO_SHAPE_TYPE
        except ImportError as exc:
            raise RuntimeError("python-pptx 未安装，请运行 `pip install python-pptx`") from exc

        prs = Presentation(io.BytesIO(data))

        # 逐页提取文本
        parts: list[str] = []
        page_count = len(prs.slides)
        images: list[ImageInfo] = []

        for slide_num, slide in enumerate(prs.slides, start=1):
            slide_texts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = para.text.strip()
                        if text:
                            slide_texts.append(text)
                # 提取内嵌图片
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    try:
                        image_bytes = shape.image.blob
                        images.append(
                            ImageInfo(
                                page=slide_num,
                                data=image_bytes,
                                order=len(images),
                            )
                        )
                    except Exception:
                        continue
            if slide_texts:
                parts.append(f"--- Slide {slide_num} ---\n" + "\n".join(slide_texts))

        text = "\n\n".join(parts)

        return ParseResult(
            char_count=len(text),
            page_count=page_count,
            excerpt=text[:200],
            text=text,
            images=images if images else None,
        )
