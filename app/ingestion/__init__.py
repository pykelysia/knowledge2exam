"""能力层：入（解析 / OCR / 切块 / 嵌入）。"""

from __future__ import annotations

from app.ingestion.chunking import Chunk, Chunker
from app.ingestion.embedding import EmbeddingClient
from app.ingestion.ocr import VisionLLMOCR
from app.ingestion.parsers import get_parser
from app.ingestion.parsers.base import SUPPORTED_EXTENSIONS, ImageInfo, Parser, ParseResult, StubParser

__all__ = [
    "ImageInfo",
    "Parser",
    "ParseResult",
    "StubParser",
    "SUPPORTED_EXTENSIONS",
    "get_parser",
    "Chunk",
    "Chunker",
    "EmbeddingClient",
    "VisionLLMOCR",
]
