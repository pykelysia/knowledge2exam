"""PDF 解析器：使用 PyMuPDF / pymupdf4llm 提取文本。"""

from __future__ import annotations

import io

from app.ingestion.parsers.base import Parser, ParseResult


class PyMuPDFParser(Parser):
    """PDF 解析器，使用 pymupdf4llm 提取 Markdown 文本。"""

    async def parse(self, filename: str, data: bytes) -> ParseResult:
        try:
            import pymupdf4llm
        except ImportError as exc:
            raise RuntimeError("pymupdf4llm 未安装，请运行 `pip install pymupdf4llm`") from exc

        # pymupdf4llm 接受文件路径或文件流
        doc = pymupdf4llm.Document(io.BytesIO(data))

        # 逐页提取文本，记录页码
        full_text_parts: list[str] = []
        page_count = len(doc)
        has_text = False

        for page_num in range(page_count):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                has_text = True
            full_text_parts.append(f"--- Page {page_num + 1} ---\n{text}")

        doc.close()

        text = "\n\n".join(full_text_parts)

        # 如果没有提取到任何文本，可能是扫描件 PDF
        if not has_text:
            text = f"（扫描版 PDF {filename}，共 {page_count} 页，无文本层）"

        return ParseResult(
            char_count=len(text),
            page_count=page_count,
            excerpt=text[:200],
            text=text,
        )
