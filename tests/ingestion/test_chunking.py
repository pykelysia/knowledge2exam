"""chunking 模块单元测试：统一分块策略（结构优先、大小兜底）。"""

from __future__ import annotations

import pytest

from app.ingestion.chunking import Chunker


class TestParameterValidation:
    def test_non_positive_chunk_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="chunk_size"):
            Chunker(chunk_size=0)
        with pytest.raises(ValueError, match="chunk_size"):
            Chunker(chunk_size=-1)

    def test_negative_overlap_rejected(self) -> None:
        with pytest.raises(ValueError, match="overlap"):
            Chunker(chunk_size=10, overlap=-1)


class TestStructure:
    def test_heading_path_per_section(self) -> None:
        chunker = Chunker(chunk_size=3, overlap=0)
        text = "# 第二章\n\n第一段\n\n## 2.3 碰撞\n\n第二段"

        chunks = chunker.chunk(text)

        assert [c.text for c in chunks] == ["第一段", "第二段"]
        assert chunks[0].heading_path == "第二章"
        assert chunks[1].heading_path == "第二章 > 2.3 碰撞"

    def test_heading_lines_excluded_from_body(self) -> None:
        chunker = Chunker(chunk_size=100, overlap=0)
        chunks = chunker.chunk("# 标题\n\n正文内容")
        assert len(chunks) == 1
        assert chunks[0].text == "正文内容"
        assert "#" not in chunks[0].text

    def test_heading_change_forces_new_chunk(self) -> None:
        """chunk 不跨小节：即使预算足够，小节边界也强制出块。"""
        chunker = Chunker(chunk_size=100, overlap=0)
        chunks = chunker.chunk("# A\n\npa\n\n# B\n\npb")
        assert [(c.text, c.heading_path) for c in chunks] == [("pa", "A"), ("pb", "B")]

    def test_heading_stack_pops_on_higher_level(self) -> None:
        chunker = Chunker(chunk_size=3, overlap=0)
        text = "# A\n\n## B\n\np1\n\n# C\n\np2"

        chunks = chunker.chunk(text)

        assert chunks[0].heading_path == "A > B"
        assert chunks[1].heading_path == "C"

    def test_plain_text_has_no_heading(self) -> None:
        chunker = Chunker(chunk_size=100, overlap=0)
        chunks = chunker.chunk("没有结构的普通文本。")
        assert chunks[0].heading_path is None
        assert chunks[0].embedding_text == chunks[0].text

    def test_empty_and_heading_only_documents_yield_no_chunks(self) -> None:
        chunker = Chunker(chunk_size=100, overlap=10)
        assert chunker.chunk("") == []
        assert chunker.chunk("\n\n  \n") == []
        assert chunker.chunk("# 只有标题\n\n## 没有正文") == []


class TestMergeAndOverlap:
    def test_short_document_single_chunk(self) -> None:
        chunker = Chunker(chunk_size=50, overlap=5)
        chunks = chunker.chunk("第一段\n\n第二段")
        assert len(chunks) == 1
        assert chunks[0].text == "第一段\n\n第二段"

    def test_paragraphs_merge_within_budget(self) -> None:
        chunker = Chunker(chunk_size=30, overlap=0)
        chunks = chunker.chunk("a" * 10 + "\n\n" + "b" * 10)
        assert len(chunks) == 1
        assert chunks[0].text == "a" * 10 + "\n\n" + "b" * 10

    def test_uniform_tail_overlap_prefixes_next_chunk(self) -> None:
        chunker = Chunker(chunk_size=20, overlap=5)
        chunks = chunker.chunk("a" * 15 + "\n\n" + "b" * 15)

        assert len(chunks) == 2
        assert chunks[0].text == "a" * 15
        # 上块末段尾部 5 字符作为下块前缀（统一尾部重叠）
        assert chunks[1].text == "a" * 5 + "\n\n" + "b" * 15

    def test_zero_overlap_disables_prefix(self) -> None:
        chunker = Chunker(chunk_size=20, overlap=0)
        chunks = chunker.chunk("a" * 15 + "\n\n" + "b" * 15)
        assert chunks[1].text == "b" * 15

    def test_all_chunks_within_body_budget(self) -> None:
        chunker = Chunker(chunk_size=20, overlap=5)
        text = "\n\n".join("段" * 12 + str(i) for i in range(10))
        chunks = chunker.chunk(text)
        assert len(chunks) > 1
        # 预算只约束正文：块长上限 = 正文预算 + 重叠前缀
        for chunk in chunks:
            assert len(chunk.text) <= chunker.overlap + chunker.chunk_size


class TestRecursiveSplit:
    def test_oversized_paragraph_splits_at_sentence_boundaries(self) -> None:
        chunker = Chunker(chunk_size=30, overlap=0)
        sentence = "0123456789。"
        text = sentence * 5  # 单段 55 字符，超限

        chunks = chunker.chunk(text)

        assert [c.text for c in chunks] == [sentence * 2, sentence * 2, sentence]
        assert all(len(c.text) <= chunker.chunk_size for c in chunks)

    def test_oversized_paragraph_without_punctuation_hard_cuts(self) -> None:
        chunker = Chunker(chunk_size=20, overlap=0)
        text = "x" * 50

        chunks = chunker.chunk(text)

        assert [len(c.text) for c in chunks] == [20, 20, 10]
        # 无重叠时片段与原文区间一一对应
        assert all(text[c.char_start : c.char_end] == c.text for c in chunks)

    def test_split_offsets_are_contiguous(self) -> None:
        chunker = Chunker(chunk_size=30, overlap=0)
        text = "0123456789。" * 5

        chunks = chunker.chunk(text)

        assert [(c.char_start, c.char_end) for c in chunks] == [(0, 22), (22, 44), (44, 55)]


class TestCharOffsets:
    def test_offsets_skip_separators_and_do_not_regress(self) -> None:
        chunker = Chunker(chunk_size=20, overlap=0)
        text = "a" * 15 + "\n\n" + "b" * 15

        chunks = chunker.chunk(text)

        assert chunks[0].char_start == 0
        assert chunks[0].char_end == 15
        # 区间按 chunk_index 单调不回退（空白分隔符不属任何块）
        assert chunks[1].char_start == text.index("b" * 15)
        assert chunks[1].char_start >= chunks[0].char_end

    def test_heading_offset_attributed_to_following_chunk(self) -> None:
        chunker = Chunker(chunk_size=3, overlap=0)
        text = "P0\n\n# T\n\nP1"

        chunks = chunker.chunk(text)

        assert [c.text for c in chunks] == ["P0", "P1"]
        # 标题行偏移归属其后首个 chunk：区间覆盖"标题 + 正文"
        assert chunks[1].char_start == text.index("# T")
        assert text[chunks[1].char_start : chunks[1].char_end] == "# T\n\nP1"


class TestEmbeddingText:
    def test_embedding_text_prefixes_heading_path(self) -> None:
        chunker = Chunker(chunk_size=100, overlap=0)
        chunks = chunker.chunk("# 第二章\n\n正文")
        assert chunks[0].embedding_text == "第二章\n正文"

    def test_embedding_text_joins_nested_headings(self) -> None:
        chunker = Chunker(chunk_size=3, overlap=0)
        chunks = chunker.chunk("# A\n\n## B\n\np")
        assert chunks[0].embedding_text == "A > B\np"

    def test_embedding_text_falls_back_to_text(self) -> None:
        chunker = Chunker(chunk_size=100, overlap=0)
        chunks = chunker.chunk("无标题正文")
        assert chunks[0].embedding_text == chunks[0].text


class TestChunkIndex:
    def test_chunk_index_globally_consecutive(self) -> None:
        chunker = Chunker(chunk_size=20, overlap=5)
        chunks = chunker.chunk("\n\n".join("段落内容" * 6 + str(i) for i in range(8)))
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
