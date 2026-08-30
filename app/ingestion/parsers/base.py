"""文档解析抽象接口。

首版 stub：不真正解析 docx/pptx/pdf，仅返回基于文本长度的模拟预览。
真实实现（PyMuPDF / python-docx / python-pptx，见 tech-selection.md 第 1 节）
后续在此接入。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 允许的文件扩展名（prd.md 第 2 节）
SUPPORTED_EXTENSIONS = {
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".pdf",
    ".md",
    ".txt",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
}


@dataclass
class ImageInfo:
    """文档内嵌图片信息。"""
    page: int | None = None
    data: bytes | None = None
    bbox: tuple[float, float, float, float] | None = None
    order: int = 0
    ocr_text: str | None = None
    skipped_reason: str | None = None


@dataclass
class ParseResult:
    char_count: int
    page_count: int | None
    excerpt: str
    text: str
    images: list[ImageInfo] | None = None


class Parser:
    """解析器抽象接口。"""

    async def parse(self, filename: str, data: bytes) -> ParseResult:  # pragma: no cover
        raise NotImplementedError


class StubParser(Parser):
    """首版 stub：返回基于文件大小的模拟解析结果。"""

    async def parse(self, filename: str, data: bytes) -> ParseResult:
        text = data.decode("utf-8", errors="ignore")
        if not text.strip():
            text = f"（二进制文件 {filename}，共 {len(data)} 字节）"
        char_count = len(text)
        excerpt = text[:200]
        return ParseResult(
            char_count=char_count,
            page_count=None,
            excerpt=excerpt,
            text=text,
        )


def supported_format(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS
