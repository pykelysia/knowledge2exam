"""能力层：入（解析 / OCR / 切块 / 嵌入）。

首版 stub，接口见 architecture.md 第 2 节模块划分。
"""

from app.ingestion.parsers.base import Parser, StubParser

__all__ = ["Parser", "StubParser"]
