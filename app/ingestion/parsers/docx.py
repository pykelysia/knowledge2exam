"""DOCX 解析器：使用 python-docx 提取文本。"""

from __future__ import annotations

import io

from app.ingestion.parsers.base import Parser, ParseResult


class DocxParser(Parser):
    """DOCX 解析器，提取段落文本。"""

    async def parse(self, filename: str, data: bytes) -> ParseResult:
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("python-docx 未安装，请运行 `pip install python-docx`") from exc

        doc = Document(io.BytesIO(data))

        # 提取所有段落文本
        parts: list[str] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)

        # 也提取表格内容
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    parts.append(row_text)

        text = "\n\n".join(parts)

        return ParseResult(
            char_count=len(text),
            page_count=None,
            excerpt=text[:200],
            text=text,
        )
