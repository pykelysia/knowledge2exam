"""chunking 模块单元测试。"""

from __future__ import annotations

import pytest

from app.core.enums import SourceType
from app.ingestion.chunking import Chunk, Chunker


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
