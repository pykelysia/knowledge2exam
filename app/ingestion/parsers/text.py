"""纯文本解析器：MD / TXT 直读。"""

from __future__ import annotations

from app.ingestion.parsers.base import Parser, ParseResult


class PlainTextParser(Parser):
    """纯文本 / Markdown 解析器，无需转换。"""

    def parse(self, filename: str, data: bytes) -> ParseResult:
        # 尝试 UTF-8 解码，失败则使用 latin-1
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")

        return ParseResult(
            char_count=len(text),
            page_count=None,
            text=text,
        )
