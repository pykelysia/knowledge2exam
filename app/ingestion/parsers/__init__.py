"""按格式分流的解析器。

docx/pptx/图片统一先规范化为 PDF（见 convert.py），再走 PDF 混合解析
（页级体检 + 损坏页视觉 OCR 兜底）；soffice 缺失或转换失败时回落原生
解析器。md/txt 为干净文本，直接读取。
"""

from __future__ import annotations

from pathlib import Path

from app.ingestion.convert import ConvertedToPdfParser
from app.ingestion.parsers.base import (
    SUPPORTED_EXTENSIONS as SUPPORTED_EXTENSIONS,
)
from app.ingestion.parsers.base import (
    Parser,
)
from app.ingestion.parsers.base import (
    ParseResult as ParseResult,
)
from app.ingestion.parsers.docx import DocxParser
from app.ingestion.parsers.image import ImageParser
from app.ingestion.parsers.pdf import PyMuPDFParser
from app.ingestion.parsers.pptx import PPTXParser
from app.ingestion.parsers.text import PlainTextParser

# 解析器单例映射（避免重复初始化）
_PARSERS: dict[str, Parser] = {}


def get_parser(filename: str) -> Parser:
    """根据文件扩展名获取对应解析器。"""
    ext = Path(filename).suffix.lower()
    if ext not in _PARSERS:
        if ext == ".pdf":
            _PARSERS[ext] = PyMuPDFParser()
        elif ext == ".docx":
            _PARSERS[ext] = ConvertedToPdfParser(fallback=DocxParser())
        elif ext == ".pptx":
            _PARSERS[ext] = ConvertedToPdfParser(fallback=PPTXParser())
        elif ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            _PARSERS[ext] = ConvertedToPdfParser(fallback=ImageParser())
        elif ext in {".md", ".txt"}:
            _PARSERS[ext] = PlainTextParser()
        else:
            raise ValueError(f"Unsupported file format: {ext}")
    return _PARSERS[ext]
