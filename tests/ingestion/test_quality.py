"""页文本质量体检单测。"""

from __future__ import annotations

from app.ingestion.quality import page_needs_ocr


class TestPageNeedsOcr:
    def test_clean_text_is_healthy(self) -> None:
        need, reason = page_needs_ocr("设函数 f(x) 在 [0,1] 上连续，证明结论成立。")
        assert (need, reason) == (False, "")

    def test_replacement_chars_trigger_garbled(self) -> None:
        text = "正常文字" + "�" * 10 + "更多正常文字"
        assert page_needs_ocr(text) == (True, "garbled")

    def test_hebrew_artifacts_trigger_garbled(self) -> None:
        # CMap 损坏时积分号常被错映射成希伯来字符（如 ׬）
        assert page_needs_ocr("定积分 ׬׬׬ 计算如下") == (True, "garbled")

    def test_single_suspicious_char_tolerated(self) -> None:
        need, reason = page_needs_ocr("文中偶见一个 ׬ 字符，整体仍然健康可读")
        assert (need, reason) == (False, "")

    def test_private_use_area_triggers_garbled(self) -> None:
        assert page_needs_ocr("私有区字符\ue000\ue001\ue002 结束") == (True, "garbled")

    def test_math_alphanumeric_is_legal(self) -> None:
        # 数学字母数字区（U+1D400+）是合法可读字符，不算异常
        need, _ = page_needs_ocr("当 𝑥→0 时 𝑓(𝑥) 连续")
        assert need is False

    def test_empty_text(self) -> None:
        assert page_needs_ocr("   \n\t") == (True, "no_text")
