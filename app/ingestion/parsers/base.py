"""文档解析抽象接口。

CPU 型解析器（pdf/docx/pptx/text）以同步方式实现 parse；
需要 LLM 调用的解析器（image）以异步方式实现。
统一经 parse_async 调用：同步实现会被放入线程池，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path

# 允许的文件扩展名。
# 注意：必须与 parsers/__init__.py 的 get_parser 映射保持一致，
# .doc/.ppt 旧格式暂不支持（无 LibreOffice 转换链路），不要加入。
SUPPORTED_EXTENSIONS = {
    ".docx",
    ".pptx",
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
    text: str
    images: list[ImageInfo] | None = None


class Parser:
    """解析器抽象接口。"""

    def parse(self, filename: str, data: bytes) -> ParseResult:  # pragma: no cover
        raise NotImplementedError

    async def parse_async(self, filename: str, data: bytes) -> ParseResult:
        """统一调用入口：异步实现直接 await，同步实现放入线程池执行。"""
        if inspect.iscoroutinefunction(self.parse):
            return await self.parse(filename, data)
        return await asyncio.to_thread(self.parse, filename, data)


def supported_format(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS
