"""图片解析器：使用视觉 LLM 做 OCR。"""

from __future__ import annotations

import io

from app.ingestion.ocr import VisionLLMOCR
from app.ingestion.parsers.base import ImageInfo, Parser, ParseResult


class ImageParser(Parser):
    """图片解析器，使用视觉 LLM 提取文本。"""

    async def parse(self, filename: str, data: bytes) -> ParseResult:
        # 1. 图片预处理：压缩/降采样
        processed_data = _preprocess_image(data)

        # 2. 调用视觉 LLM 提取文本
        ocr = VisionLLMOCR.from_settings()
        ocr_text = None
        try:
            ocr_text = await ocr.extract_text(processed_data)
        except Exception as exc:
            raise RuntimeError(f"图片 OCR 失败: {exc}") from exc

        if not ocr_text or not ocr_text.strip():
            ocr_text = f"（图片 {filename}，OCR 未识别出有效文本）"

        image_info = ImageInfo(
            page=None,
            data=data,
            order=0,
            ocr_text=ocr_text.strip(),
        )

        return ParseResult(
            char_count=len(ocr_text.strip()),
            page_count=None,
            excerpt=ocr_text.strip()[:200],
            text=ocr_text.strip(),
            images=[image_info],
        )


def _preprocess_image(data: bytes, max_size: int = 1024) -> bytes:
    """压缩图片至最大边 max_size，减少 OCR 成本；按 EXIF 信息矫正方向。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)

    # 转换为 RGB（处理 RGBA / palette 等）
    if img.mode != "RGB":
        img = img.convert("RGB")

    width, height = img.size
    if max(width, height) > max_size:
        ratio = max_size / max(width, height)
        new_size = (int(width * ratio), int(height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()
