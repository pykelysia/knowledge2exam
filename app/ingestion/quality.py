"""PDF 页文本质量体检。

判断一页从文本层提取出的内容是否可信赖：
- 干净文本：直接使用，零 LLM 成本；
- 损坏文本：字体缺 ToUnicode CMap 时 PyMuPDF 会产出 U+FFFD 或错映射字符
  （典型如积分号 ∫ 被映射成希伯来字母 ׬），公式也被排版流拆成碎片，
  这类页交给视觉 LLM OCR 兜底；
- 空文本：扫描页 / 无文本层，同样交给视觉 LLM。
"""

from __future__ import annotations

# U+FFFD（replacement character）占页面字符数的比例阈值
REPLACEMENT_RATIO_THRESHOLD = 0.02
# 异常字符数量阈值：中文文档里出现即视为 CMap 损坏特征
SUSPICIOUS_CHAR_THRESHOLD = 3

# CMap 损坏时常见错映射的 Unicode 区块 + 私用区（字体自定义编码的常见落点）
_SUSPICIOUS_RANGES: tuple[tuple[int, int], ...] = (
    (0x0590, 0x05FF),  # Hebrew
    (0x0700, 0x074F),  # Syriac
    (0xE000, 0xF8FF),  # Private Use Area
)


def page_needs_ocr(text: str) -> tuple[bool, str]:
    """体检单页文本，返回 (是否需要 OCR, 原因)。

    原因取值：""（健康）/ "no_text"（无文本层）/ "garbled"（乱码）。
    """
    stripped = text.strip()
    if not stripped:
        return True, "no_text"

    total = len(stripped)
    if stripped.count("\ufffd") / total >= REPLACEMENT_RATIO_THRESHOLD:
        return True, "garbled"

    suspicious = sum(
        1
        for ch in stripped
        if any(lo <= ord(ch) <= hi for lo, hi in _SUSPICIOUS_RANGES)
    )
    if suspicious >= SUSPICIOUS_CHAR_THRESHOLD:
        return True, "garbled"

    return False, ""
