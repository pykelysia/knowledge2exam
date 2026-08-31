"""按格式分流的解析器。"""

from __future__ import annotations

from pathlib import Path

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
            _PARSERS[ext] = DocxParser()
        elif ext == ".pptx":
            _PARSERS[ext] = PPTXParser()
        elif ext in {".md", ".txt"}:
            _PARSERS[ext] = PlainTextParser()
        elif ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            _PARSERS[ext] = ImageParser()
        else:
            raise ValueError(f"Unsupported file format: {ext}")
    return _PARSERS[ext]
