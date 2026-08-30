"""postprocess 模块单元测试。"""

from __future__ import annotations

import math

import pytest

from app.ingestion.parsers.base import ImageInfo
from app.ingestion.postprocess import (
    _is_continuous,
    _jaccard_similarity,
    _mark_gaps,
    _merge_continuous,
    _deduplicate,
    build_merged_text,
    process_images,
)


# ---------- 工具函数 ----------

def _make_image(order: int, ocr_text: str | None = None, data: bytes | None = None) -> ImageInfo:
    """构造测试用 ImageInfo。"""
    return ImageInfo(page=1, data=data or b"fake", order=order, ocr_text=ocr_text)


# ---------- _jaccard_similarity ----------


class TestJaccardSimilarity:
    def test_identical(self) -> None:
        assert _jaccard_similarity("abc", "abc") == 1.0

    def test_no_overlap(self) -> None:
        assert _jaccard_similarity("abc", "def") == 0.0

    def test_partial_overlap(self) -> None:
        sim = _jaccard_similarity("abcdef", "cdefgh")
        # bigrams: ab, bc, cd, de, ef vs cd, de, ef, fg, gh → 3/7
        expected = 3 / 7
        assert abs(sim - expected) < 0.01

    def test_empty_strings(self) -> None:
        assert _jaccard_similarity("", "") == 1.0
        assert _jaccard_similarity("abc", "") == 0.0
        assert _jaccard_similarity("", "abc") == 0.0


# ---------- _is_continuous ----------


class TestIsContinuous:
    def test_ends_with_period_not_continuous(self) -> None:
        assert not _is_continuous("这是第一段。", "这是第二段。")

    def test_ends_with_comma_continuous(self) -> None:
        assert _is_continuous("这是第一段，", "这是第二段")

    def test_empty_prev_not_continuous(self) -> None:
        assert not _is_continuous("", "第二段")

    def test_empty_curr_not_continuous(self) -> None:
        assert not _is_continuous("第一段", "")

    def test_no_punctuation_continuous(self) -> None:
        assert _is_continuous("第一段内容", "第二段内容")


# ---------- _deduplicate ----------


class TestDeduplicate:
    def test_duplicate_identical_texts(self) -> None:
        images = [
            _make_image(0, "这是第一张图片的文字内容描述"),
            _make_image(1, "这是第一张图片的文字内容描述"),
        ]
        _deduplicate(images)  # type: ignore[arg-type]
        assert images[1]._deduped is True

    def test_different_texts_not_deduped(self) -> None:
        images = [
            _make_image(0, "第一张图片的内容"),
            _make_image(1, "完全不同的第二张图片"),
        ]
        _deduplicate(images)  # type: ignore[arg-type]
        assert not getattr(images[0], "_deduped", False)
        assert not getattr(images[1], "_deduped", False)


# ---------- _merge_continuous ----------


class TestMergeContinuous:
    def test_merge_two_continuous(self) -> None:
        images = [
            _make_image(0, "第一段内容，"),
            _make_image(1, "接着是第二段"),
        ]
        _merge_continuous(images)  # type: ignore[arg-type]
        assert images[0].ocr_text == "第一段内容，\n接着是第二段"
        assert images[1]._merged is True

    def test_no_merge_when_discontinuous(self) -> None:
        images = [
            _make_image(0, "第一段内容。"),
            _make_image(1, "第二段内容"),
        ]
        _merge_continuous(images)  # type: ignore[arg-type]
        assert images[0].ocr_text == "第一段内容。"
        assert not getattr(images[1], "_merged", False)

    def test_skip_skipped_images(self) -> None:
        images = [
            _make_image(0, "第一段，"),
            _make_image(1, "第二段"),
        ]
        images[0]._skipped = True
        _merge_continuous(images)  # type: ignore[arg-type]
        assert not getattr(images[0], "_merged", False)
        assert not getattr(images[1], "_merged", False)


# ---------- _mark_gaps ----------


class TestMarkGaps:
    def test_inserts_gap_between_discontinuous(self) -> None:
        images = [
            _make_image(0, "第一段。"),
            _make_image(1, "第二段"),
        ]
        initial_len = len(images)
        _mark_gaps(images)  # type: ignore[arg-type]
        assert len(images) == initial_len + 1
        gap_imgs = [img for img in images if getattr(img, "_gap_marker", None)]
        assert len(gap_imgs) == 1
        assert gap_imgs[0].ocr_text == "[--- 内容缺失 ---]"

    def test_no_gap_between_continuous(self) -> None:
        images = [
            _make_image(0, "第一段，"),
            _make_image(1, "第二段"),
        ]
        initial_len = len(images)
        _mark_gaps(images)  # type: ignore[arg-type]
        assert len(images) == initial_len


# ---------- process_images 集成 ----------


class TestProcessImages:
    def test_empty_list(self) -> None:
        result = process_images([])
        assert result == []

    def test_full_pipeline_discontinuous(self) -> None:
        """两张不连续图片，中间应有间隙。"""
        images = [
            _make_image(0, "第一章的内容总结。"),
            _make_image(1, "第二章开始讲解新的知识点"),
        ]
        # 模拟已通过预筛选和 OCR（跳过 OCR 调用）
        for img in images:
            img._skipped = False
            img.ocr_text = img.ocr_text  # 已有文本，_run_ocr 会跳过
        process_images(images)
        gap_imgs = [img for img in images if getattr(img, "_gap_marker", None)]
        assert len(gap_imgs) == 1

    def test_build_merged_text(self) -> None:
        images = [
            _make_image(0, "第一段内容"),
            _make_image(1, "第二段内容"),
        ]
        images[1]._deduped = True
        text = build_merged_text(images)
        assert "第一段内容" in text
        assert "第二段内容" not in text


# ---------- 边界条件 ----------


class TestEdgeCases:
    def test_single_image_no_dedup(self) -> None:
        images = [_make_image(0, "单张图片")]
        _deduplicate(images)  # type: ignore[arg-type]
        assert not getattr(images[0], "_deduped", False)

    def test_merge_three_continuous(self) -> None:
        images = [
            _make_image(0, "第一段，"),
            _make_image(1, "第二段，"),
            _make_image(2, "第三段"),
        ]
        _merge_continuous(images)  # type: ignore[arg-type]
        assert images[0]._merged_count == 3
        assert images[1]._merged is True
        assert images[2]._merged is True
