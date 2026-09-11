"""图片 OCR 后处理。

对文档中提取的内嵌图片做三段处理：
1. 预筛选 — 跳过装饰性图片（尺寸过小、纯色等）
2. 去重   — 相邻图片 OCR 文本相似度 > 阈值视为重复，保留较长者
3. 跨图合并 — 相邻图片 OCR 文本语义连续则合并为一段，减少碎片
4. 间隙标记 — 非连续且非重复的相邻图片之间插入间隙标记

公共接口：process_images(images) -> list[ImageInfo]
"""

from __future__ import annotations

import asyncio
import io
import math

from app.ingestion.ocr import VisionLLMOCR
from app.ingestion.parsers.base import ImageInfo

# ---------- 阈值配置 ----------

_MIN_IMAGE_SIZE = 50          # 最小边长（像素），低于此视为装饰
_COLOR_VAR_THRESHOLD = 10     # 色彩方差低于此视为纯色/近纯色
_DEDUP_SIMILARITY = 0.8       # Jaccard 相似度高于此视为重复
_OCR_TIMEOUT_SECONDS = 60     # 单张图片 OCR 超时


# ---------- 公共接口 ----------


def process_images(images: list[ImageInfo]) -> list[ImageInfo]:
    """执行完整后处理流水线。

    原地修改传入的 ImageInfo 列表，同时返回处理后的列表。
    """
    if not images:
        return images

    # 1. 预筛选
    _prefilter(images)

    # 2. OCR（仅对未跳过的图片）
    remaining = [img for img in images if img.skipped_reason is None]
    if remaining:
        _run_ocr(remaining)

    # 3. 去重（基于 OCR 文本）
    _deduplicate(images)

    # 4. 跨图合并
    _merge_continuous(images)

    # 5. 间隙标记
    _mark_gaps(images)

    return images


def build_merged_text(images: list[ImageInfo], text_separator: str = "\n\n") -> str:
    """将后处理后的图片 OCR 文本拼接为一段文本，用于和文档文本合并。

    跳过被预筛选和去重的图片，保留合并组文本和间隙标记。
    """
    parts: list[str] = []
    for img in images:
        if getattr(img, "_skipped", False):
            continue
        if getattr(img, "_deduped", False):
            continue
        if getattr(img, "_merged", False):
            continue
        if img.ocr_text:
            parts.append(img.ocr_text)
        elif getattr(img, "_gap_marker", None):
            parts.append(img._gap_marker)
    return text_separator.join(p for p in parts if p)


# ---------- Step 1：预筛选 ----------


def _prefilter(images: list[ImageInfo]) -> None:
    """跳过装饰性图片。"""
    for img in images:
        if img.data is None:
            img.skipped_reason = "no_data"
            img._skipped = True
            continue

        try:
            from PIL import Image
        except ImportError:
            continue

        try:
            pil_img = Image.open(io.BytesIO(img.data))
            w, h = pil_img.size
            if w < _MIN_IMAGE_SIZE or h < _MIN_IMAGE_SIZE:
                img.skipped_reason = f"too_small:{w}x{h}"
                img._skipped = True
                continue

            # 色彩方差检查（灰度直方图标准差）
            gray = pil_img.convert("L")
            hist = gray.histogram()
            pixel_count = sum(hist)
            if pixel_count == 0:
                img.skipped_reason = "empty"
                img._skipped = True
                continue
            mean = sum(i * v for i, v in enumerate(hist)) / pixel_count
            variance = sum(v * (i - mean) ** 2 for i, v in enumerate(hist)) / pixel_count
            std = math.sqrt(variance)
            if std < _COLOR_VAR_THRESHOLD:
                img.skipped_reason = f"low_variance:{std:.1f}"
                img._skipped = True
        except Exception:
            # 无法解析的图片保留，让 OCR 处理
            pass


# ---------- Step 2：OCR ----------


def _run_ocr(images: list[ImageInfo]) -> None:
    """对图片列表执行 OCR。

    调用方应处于无运行中事件循环的上下文（如 to_thread 工作线程），
    此处用 asyncio.run + wait_for 控制单张超时，超时即取消底层请求。
    """
    try:
        ocr = VisionLLMOCR.from_settings()
    except Exception as exc:
        for img in images:
            if not getattr(img, "_skipped", False) and not img.ocr_text:
                img.skipped_reason = f"ocr_unavailable:{exc}"
                img._skipped = True
        return

    for img in images:
        if getattr(img, "_skipped", False) or img.ocr_text or img.data is None:
            continue
        try:
            img.ocr_text = asyncio.run(
                asyncio.wait_for(ocr.extract_text(img.data), timeout=_OCR_TIMEOUT_SECONDS)
            )
        except Exception as exc:
            img.skipped_reason = f"ocr_failed:{exc}"
            img._skipped = True


# ---------- Step 3：去重 ----------


def _jaccard_similarity(a: str, b: str) -> float:
    """计算字符 bigram 的 Jaccard 相似度。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    def bigrams(text: str) -> set[str]:
        return {text[i : i + 2] for i in range(len(text) - 1)}

    bg_a = bigrams(a)
    bg_b = bigrams(b)
    intersection = bg_a & bg_b
    union = bg_a | bg_b
    return len(intersection) / len(union) if union else 0.0


def _deduplicate(images: list[ImageInfo]) -> None:
    """相邻图片 OCR 文本相似度 > 阈值时，标记较短者为重复。"""
    active = [
        img for img in images
        if not getattr(img, "_skipped", False)
        and not getattr(img, "_deduped", False)
        and img.ocr_text
    ]
    if len(active) < 2:
        return

    for i in range(len(active) - 1):
        curr = active[i]
        next_img = active[i + 1]

        if getattr(next_img, "_deduped", False):
            continue

        curr_text = curr.ocr_text.strip()
        next_text = next_img.ocr_text.strip()

        if not curr_text or not next_text:
            continue

        sim = _jaccard_similarity(curr_text, next_text)
        if sim >= _DEDUP_SIMILARITY:
            # 保留较长者，标记较短者为重复
            if len(curr_text) >= len(next_text):
                next_img._deduped = True
                next_img._dedup_reason = f"duplicate_of_{curr.order}:sim={sim:.2f}"
            else:
                curr._deduped = True
                curr._dedup_reason = f"duplicate_of_{next_img.order}:sim={sim:.2f}"


# ---------- Step 4：跨图合并 ----------


def _is_continuous(prev_text: str, curr_text: str) -> bool:
    """判断两段文本是否语义连续。

    规则：前一段不以完整句号/换行结尾 → 可能连续。
    更精细的判断可引入 LLM，此处用启发式。
    """
    if not prev_text or not curr_text:
        return False

    prev_stripped = prev_text.rstrip()
    if not prev_stripped:
        return False

    last_char = prev_stripped[-1]
    # 最后一个是中文句号/英文句号，或最后两个是 ". "：语义完整结束，不连续
    if last_char in {"。", "！", "？", "；", "\n"} or prev_stripped.endswith(". "):
        return False

    return True


def _merge_continuous(images: list[ImageInfo]) -> None:
    """将连续相邻图片的 OCR 文本合并，标记合并组。"""
    active = [
        img for img in images
        if not getattr(img, "_skipped", False)
        and not getattr(img, "_deduped", False)
        and img.ocr_text
    ]
    if len(active) < 2:
        return

    group_start = 0
    for i in range(len(active) - 1):
        curr = active[i]
        next_img = active[i + 1]

        if not _is_continuous(curr.ocr_text, next_img.ocr_text):
            # 闭合当前组
            if i > group_start:
                _apply_merge(active, group_start, i)
            group_start = i + 1

    # 处理最后一组
    if len(active) - 1 > group_start:
        _apply_merge(active, group_start, len(active) - 1)


def _apply_merge(group: list[ImageInfo], start: int, end: int) -> None:
    """将 group[start..end] 合并到第一个元素，其余标记为 merged。"""
    primary = group[start]
    texts: list[str] = []
    for img in group[start : end + 1]:
        if img.ocr_text:
            texts.append(img.ocr_text)
    primary.ocr_text = "\n".join(texts)
    primary._merged_count = end - start + 1
    for img in group[start + 1 : end + 1]:
        img._merged = True
        img.ocr_text = None


# ---------- Step 5：间隙标记 ----------


_GAP_MARKER = "[--- 内容缺失 ---]"


def _mark_gaps(images: list[ImageInfo]) -> None:
    """在非连续、非重复的相邻活跃图片之间插入间隙标记。"""
    active = [
        img for img in images
        if not getattr(img, "_skipped", False)
        and not getattr(img, "_deduped", False)
        and not getattr(img, "_merged", False)
        and img.ocr_text
    ]

    for i in range(len(active) - 1):
        curr = active[i]
        next_img = active[i + 1]

        if not curr.ocr_text or not next_img.ocr_text:
            continue

        if _is_continuous(curr.ocr_text, next_img.ocr_text):
            continue

        # 插入间隙标记
        gap = ImageInfo(
            page=curr.page,
            order=curr.order,  # 临时，插入后会重新排
            ocr_text=_GAP_MARKER,
        )
        gap._gap_marker = _GAP_MARKER
        images.insert(images.index(next_img), gap)
