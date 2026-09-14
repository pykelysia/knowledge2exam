"""文本切块模块。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.core.debug_log import log_step
from app.core.enums import SourceType

# PDF 页标记（与 postprocess.replace_page_text 保持同一格式约定）
_PAGE_MARKER_RE = re.compile(r"^--- Page (\d+) ---[ \t]*$", re.MULTILINE)


@dataclass
class Chunk:
    """切块结果。"""
    text: str
    page: int | None = None
    chunk_index: int = 0
    resource_id: uuid.UUID | None = None
    upload_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    school_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    source_type: SourceType | None = None
    is_shared: bool = False
    # 嵌入向量由预处理阶段计算后绑定；upsert 依赖此字段，缺失即入库失败
    embedding: list[float] | None = None


class Chunker:
    """文本切块器。

    按内容类型选择不同策略：
    - PPTX：每页一 chunk，超长再切
    - PDF：按语义段落，跨页合并
    - DOCX/MD：按标题层级
    - 图片OCR：全文一 chunk，超长再切
    """

    def __init__(self, chunk_size: int = 700, overlap: int = 100) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(
        self,
        text: str,
        page: int | None = None,
        source_type: SourceType | None = None,
    ) -> list[Chunk]:
        """将文本切分为多个 chunk。"""
        chunks = []
        if source_type == SourceType.lecture and page is not None:
            # PPTX：每页一 chunk，超长再切
            chunks = self._chunk_by_page(text, page, source_type)
        elif source_type == SourceType.book:
            # PDF：按语义段落，跨页合并
            chunks = self._chunk_by_paragraph(text, page, source_type)
        else:
            # DOCX / 其他：按大小切
            chunks = self._chunk_by_size(text, page, source_type)

        if chunks:
            log_step(
                job_id="",
                name="chunker.chunk",
                stage="preprocessing",
                input={
                    "text_chars": len(text),
                    "source_type": source_type.value if source_type else None,
                },
                output={"chunk_count": len(chunks)},
            )

        return chunks

    def chunk_pdf(self, text: str, source_type: SourceType | None = None) -> list[Chunk]:
        """PDF 文本切块：按 `--- Page N ---` 标记分段，chunk.page 精确记页。

        页界即块界（与 PPTX 每页一块的策略一致）：段落只在页内合并，
        跨页段落不回溯 overlap——换取 chunk.page 与内容所在页严格对应，
        便于检索命中后引用页码。无页标记的文本回退 chunk(page=None)。
        """
        if "--- Page " not in text:
            return self.chunk(text, page=None, source_type=source_type)

        matches = list(_PAGE_MARKER_RE.finditer(text))
        segments: list[tuple[int | None, str]] = []
        if matches:
            head = text[: matches[0].start()].strip()
            if head:
                segments.append((None, head))
            for i, match in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                body = text[match.end() : end].strip()
                segments.append((int(match.group(1)), body))
        else:
            segments.append((None, text))

        chunks: list[Chunk] = []
        for page, body in segments:
            paragraphs = [(page, p.strip()) for p in body.split("\n\n") if p.strip()]
            chunks.extend(self._merge_paragraphs(paragraphs, source_type))
        # chunk_index 全局连续（入库幂等键为 (resource_id, chunk_index)）
        for index, chunk in enumerate(chunks):
            chunk.chunk_index = index
        return chunks

    def _chunk_by_page(
        self,
        text: str,
        page: int,
        source_type: SourceType | None = None,
    ) -> list[Chunk]:
        """PPTX 切块：每页一个 chunk，超长再切。"""
        chunks: list[Chunk] = []
        if len(text) <= self.chunk_size:
            chunks.append(Chunk(text=text, page=page, chunk_index=0, source_type=source_type))
        else:
            # 超长页面按大小切
            parts = self._split_by_size(text)
            for idx, part in enumerate(parts):
                chunks.append(Chunk(text=part, page=page, chunk_index=idx, source_type=source_type))
        return chunks

    def _chunk_by_paragraph(
        self,
        text: str,
        page: int | None = None,
        source_type: SourceType | None = None,
    ) -> list[Chunk]:
        """PDF 切块：按语义段落合并。"""
        paragraphs = [(page, p.strip()) for p in text.split("\n\n") if p.strip()]
        return self._merge_paragraphs(paragraphs, source_type)

    def _merge_paragraphs(
        self,
        paragraphs: list[tuple[int | None, str]],
        source_type: SourceType | None = None,
    ) -> list[Chunk]:
        """段落贪心合并至接近 chunk_size；chunk.page 取首段页码。"""
        chunks: list[Chunk] = []
        current: list[tuple[int | None, str]] = []
        current_len = 0

        for page, para in paragraphs:
            if current_len + len(para) > self.chunk_size and current:
                chunk_text = "\n\n".join(p for _, p in current)
                chunks.append(
                    Chunk(
                        text=chunk_text,
                        page=current[0][0],
                        chunk_index=len(chunks),
                        source_type=source_type,
                    )
                )
                # 保留 overlap
                overlap_page, overlap_text = current[-1]
                current = ([(overlap_page, overlap_text)] if overlap_text else []) + [
                    (page, para)
                ]
                current_len = sum(len(p) for _, p in current)
            else:
                current.append((page, para))
                current_len += len(para)

        if current:
            chunks.append(
                Chunk(
                    text="\n\n".join(p for _, p in current),
                    page=current[0][0],
                    chunk_index=len(chunks),
                    source_type=source_type,
                )
            )

        return chunks

    def _chunk_by_size(
        self,
        text: str,
        page: int | None = None,
        source_type: SourceType | None = None,
    ) -> list[Chunk]:
        """通用切块：按大小切分。"""
        parts = self._split_by_size(text)
        return [
            Chunk(text=part, page=page, chunk_index=idx, source_type=source_type)
            for idx, part in enumerate(parts)
        ]

    def _split_by_size(self, text: str) -> list[str]:
        """将文本按 chunk_size / overlap 切分为多个片段。"""
        if len(text) <= self.chunk_size:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + self.chunk_size
            if end >= len(text):
                chunks.append(text[start:])
                break
            # 尝试在句号/换行处断开
            break_pos = text.rfind("。", start, end)
            if break_pos == -1:
                break_pos = text.rfind("\n", start, end)
            if break_pos == -1 or break_pos <= start:
                break_pos = end
            chunks.append(text[start:break_pos])
            start = max(start + 1, break_pos - self.overlap)
        return chunks
