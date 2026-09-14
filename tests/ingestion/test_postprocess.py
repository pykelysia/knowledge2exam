"""postprocess 模块单元测试。"""

from __future__ import annotations

from app.ingestion.ocr import VisionLLMOCR
from app.ingestion.parsers.base import ImageInfo
from app.ingestion.postprocess import (
    _deduplicate,
    _is_continuous,
    _jaccard_similarity,
    _mark_gaps,
    _merge_continuous,
    _run_ocr,
    build_merged_text,
    inline_image_text,
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


# ---------- inline_image_text（占位符回填） ----------


def _marker_image(marker: str, ocr_text: str | None, kind: str = "image") -> ImageInfo:
    return ImageInfo(page=1, marker=marker, kind=kind, ocr_text=ocr_text)


class TestInlineImageText:
    def test_replaces_marker_in_place(self) -> None:
        text = "前文段落。\n\n[图: p1-1]\n\n后文段落。"
        out = inline_image_text(text, [_marker_image("[图: p1-1]", "图里的文字")])
        assert out is not None
        assert "> 图：图里的文字" in out
        assert "[图: p1-1]" not in out
        assert "前文段落。" in out
        assert "后文段落。" in out

    def test_chart_label(self) -> None:
        out = inline_image_text(
            "[图表: p1-1]", [_marker_image("[图表: p1-1]", "折线图", kind="chart")]
        )
        assert out is not None
        assert "> 图表：折线图" in out

    def test_multiline_ocr_quoted(self) -> None:
        out = inline_image_text("[图: p1-1]", [_marker_image("[图: p1-1]", "第一行\n第二行")])
        assert out is not None
        assert "> 图：第一行\n> 第二行" in out

    def test_skipped_marker_removed(self) -> None:
        img = _marker_image("[图: p1-1]", None)
        img._skipped = True
        out = inline_image_text("前文\n\n[图: p1-1]\n\n后文", [img])
        assert out is not None
        assert "[图: p1-1]" not in out
        assert "前文" in out and "后文" in out

    def test_deduped_marker_removed(self) -> None:
        img = _marker_image("[图: p1-1]", "重复内容")
        img._deduped = True
        out = inline_image_text("[图: p1-1]", [img])
        assert out is not None
        assert "重复内容" not in out

    def test_no_markers_returns_none(self) -> None:
        assert inline_image_text("无占位符正文", [_marker_image("[图: p1-1]", "x")]) is None
        assert inline_image_text("无占位符正文", []) is None

    def test_marker_left_out_of_text_is_ignored(self) -> None:
        # 图片有 marker 但正文没有（如该页被整页 OCR 替换）→ 不影响其他 marker 回填
        text = "正文 [图: p1-2] 引用"
        images = [
            _marker_image("[图: p1-1]", "丢失页"),
            _marker_image("[图: p1-2]", "正常图"),
        ]
        out = inline_image_text(text, images)
        assert out is not None
        assert "> 图：正常图" in out
        assert "丢失页" not in out


# ---------- _run_ocr 的 kind 分流 ----------


class _SpyOCR:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract_text(self, image_bytes: bytes) -> str:
        self.calls.append("text")
        return "T"

    async def describe_chart(self, image_bytes: bytes) -> str:
        self.calls.append("chart")
        return "C"


class TestRunOcrKindRouting:
    def test_chart_uses_describe_prompt(self, monkeypatch) -> None:
        spy = _SpyOCR()
        monkeypatch.setattr(VisionLLMOCR, "from_settings", classmethod(lambda cls: spy))
        chart = ImageInfo(page=1, data=b"chart", kind="chart")
        image = ImageInfo(page=2, data=b"image")
        _run_ocr([chart, image])
        assert spy.calls == ["chart", "text"]
        assert chart.ocr_text == "C"
        assert image.ocr_text == "T"
