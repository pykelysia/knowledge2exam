"""DOCX 解析器：python-docx 原生提取（标题/段落/表格/图片占位符）。

输出统一文本契约：`#` 层级标题 + 正文段落 + 表格 Markdown + `[图: docx-N]`
占位符（供编排层图片 OCR 原位回填）。DOCX 无页概念，不产出页码。
"""

from __future__ import annotations

import io
import re

from app.ingestion.parsers.base import ImageInfo, Parser, ParseResult
from app.ingestion.parsers.tables import rows_to_markdown

# style_id 区域无关（如 "Heading1"）；样式名随 Word 语言本地化（"标题 1"）
_HEADING_ID_RE = re.compile(r"^Heading([1-9])$")
_HEADING_NAME_RE = re.compile(r"^(?:Heading|标题)\s*([1-9])$")


class DocxParser(Parser):
    """DOCX 解析器：按文档顺序提取段落、表格与内嵌图片。"""

    def parse(self, filename: str, data: bytes) -> ParseResult:
        try:
            from docx import Document
            from docx.oxml.table import CT_Tbl
            from docx.oxml.text.paragraph import CT_P
            from docx.table import Table
            from docx.text.paragraph import Paragraph
        except ImportError as exc:
            raise RuntimeError("python-docx 未安装，请运行 `pip install python-docx`") from exc

        doc = Document(io.BytesIO(data))

        blocks: list[str] = []
        images: list[ImageInfo] = []
        marker_by_rid: dict[str, str] = {}

        for child in doc.element.body.iterchildren():
            if isinstance(child, CT_P):
                para = Paragraph(child, doc)
                text = para.text.strip()
                level = _heading_level(para)
                if text:
                    blocks.append(f"{'#' * level} {text}" if level else text)
                # 段内引用的图片（inline/anchored）就地留占位符；
                # 同一图片多次引用只建一个 ImageInfo，占位符逐次输出
                for r_id in child.xpath(".//a:blip/@r:embed"):
                    marker = self._add_image_block(doc, r_id, images, marker_by_rid)
                    if marker:
                        blocks.append(marker)
            elif isinstance(child, CT_Tbl):
                table = Table(child, doc)
                markdown = rows_to_markdown(
                    [[cell.text for cell in row.cells] for row in table.rows]
                )
                if markdown:
                    blocks.append(markdown)
                # 单元格内的插图一并提取：cell.text 不承载图片，静默丢弃即内容损失
                for r_id in child.xpath(".//a:blip/@r:embed"):
                    marker = self._add_image_block(doc, r_id, images, marker_by_rid)
                    if marker:
                        blocks.append(marker)

        text = "\n\n".join(blocks)

        return ParseResult(
            char_count=len(text),
            page_count=None,
            text=text,
            images=images or None,
        )

    @staticmethod
    def _add_image_block(
        doc, r_id: str, images: list[ImageInfo], marker_by_rid: dict[str, str]
    ) -> str:
        """登记图片并返回占位符；同一图片重复引用复用首个占位符。

        图片关系不可达时返回空串（占位符与图片一并跳过）。
        """
        if r_id in marker_by_rid:
            return marker_by_rid[r_id]
        try:
            blob = doc.part.rels[r_id].target_part.blob
        except Exception:  # noqa: BLE001
            marker_by_rid[r_id] = ""
            return ""
        marker = f"[图: docx-{len(images) + 1}]"
        marker_by_rid[r_id] = marker
        images.append(ImageInfo(data=blob, order=len(images) + 1, marker=marker))
        return marker


def _heading_level(paragraph) -> int | None:
    """按样式识别标题层级（1-6）；正文返回 None。"""
    style = paragraph.style
    match = _HEADING_ID_RE.match(getattr(style, "style_id", "") or "")
    if match is None:
        match = _HEADING_NAME_RE.match(getattr(style, "name", "") or "")
    if match is None:
        return None
    return min(int(match.group(1)), 6)
