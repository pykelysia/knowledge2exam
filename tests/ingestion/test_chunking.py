"""chunking 模块单元测试。"""

from __future__ import annotations

import pytest

from app.core.enums import SourceType
from app.ingestion.chunking import Chunker


@pytest.fixture
def chunker() -> Chunker:
    return Chunker(chunk_size=10, overlap=2)


class TestChunkBySize:
    def test_short_text(self, chunker: Chunker) -> None:
        chunks = chunker.chunk("hello", source_type=SourceType.note)
        assert len(chunks) == 1
        assert chunks[0].text == "hello"
        assert chunks[0].chunk_index == 0

    def test_exact_size(self, chunker: Chunker) -> None:
        text = "a" * 10
        chunks = chunker.chunk(text, source_type=SourceType.note)
        assert len(chunks) == 1
        assert chunks[0].text == text

    def test_split(self, chunker: Chunker) -> None:
        text = "a" * 25
        chunks = chunker.chunk(text, source_type=SourceType.note)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c.text) <= chunker.chunk_size


class TestChunkByPage:
    def test_short_page(self, chunker: Chunker) -> None:
        chunks = chunker.chunk("hello", page=1, source_type=SourceType.lecture)
        assert len(chunks) == 1
        assert chunks[0].page == 1

    def test_long_page(self, chunker: Chunker) -> None:
        text = "a" * 25
        chunks = chunker.chunk(text, page=3, source_type=SourceType.lecture)
        assert all(c.page == 3 for c in chunks)
        assert len(chunks) > 1


class TestChunkByParagraph:
    def test_paragraph_merge(self, chunker: Chunker) -> None:
        text = "a\n\nb\n\nc"
        chunks = chunker.chunk(text, source_type=SourceType.book)
        assert len(chunks) >= 1
        for c in chunks:
            assert len(c.text) <= chunker.chunk_size


class TestChunkMetadata:
    def test_metadata_propagation(self, chunker: Chunker) -> None:
        chunks = chunker.chunk("hello world", source_type=SourceType.note)
        assert chunks[0].source_type == SourceType.note
        assert chunks[0].chunk_index == 0


class TestChunkPdf:
    def test_page_metadata_propagates(self) -> None:
        chunker = Chunker(chunk_size=15, overlap=0)
        text = "--- Page 1 ---\n\npara one\n\npara two\n\n--- Page 2 ---\n\npara three"
        chunks = chunker.chunk_pdf(text, source_type=SourceType.book)

        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        # 页界即块界：chunk.page 与内容所在页严格对应
        assert [c.page for c in chunks] == [1, 1, 2]
        assert chunks[0].text == "para one"
        assert chunks[-1].text == "para three"

    def test_chunks_never_span_pages(self) -> None:
        chunker = Chunker(chunk_size=1000, overlap=0)
        text = "--- Page 3 ---\n\nalpha\n\nbeta\n\n--- Page 4 ---\n\ngamma"
        chunks = chunker.chunk_pdf(text, source_type=SourceType.book)

        assert [(c.page, c.text) for c in chunks] == [
            (3, "alpha\n\nbeta"),
            (4, "gamma"),
        ]

    def test_no_markers_falls_back(self) -> None:
        chunker = Chunker(chunk_size=1000, overlap=0)
        chunks = chunker.chunk_pdf("plain text", source_type=SourceType.book)
        assert len(chunks) == 1
        assert chunks[0].page is None
        assert chunks[0].text == "plain text"

    def test_prefix_before_first_marker_kept(self) -> None:
        chunker = Chunker(chunk_size=1000, overlap=0)
        text = "front matter\n\n--- Page 1 ---\n\nbody"
        chunks = chunker.chunk_pdf(text, source_type=SourceType.book)
        assert [(c.page, c.text) for c in chunks] == [
            (None, "front matter"),
            (1, "body"),
        ]
