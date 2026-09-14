"""图片 OCR 后处理。

对文档中提取的内嵌图片做三段处理：
1. 预筛选 — 跳过装饰性图片（尺寸过小、纯色等）
2. 去重   — 相邻图片 OCR 文本相似度 > 阈值视为重复，保留较长者
3. 跨图合并 — 相邻图片 OCR 文本语义连续则合并为一段，减少碎片
4. 间隙标记 — 非连续且非重复的相邻图片之间插入间隙标记

另提供页级 OCR 兜底（针对 PDF 损坏页/扫描页的整页渲染图）：
- run_page_ocr(renders)   — 并发调用视觉 LLM 把整页转录为 Markdown
- replace_page_text(text) — 按 `--- Page N ---` 标记把转录结果替换回文档

公共接口：process_images(images) / inline_image_text(text, images) /
run_page_ocr(renders) / replace_page_text(...)
"""

from __future__ import annotations

import asyncio
import io
import math
import re

from app.config import settings
from app.ingestion.ocr import VisionLLMOCR
from app.ingestion.parsers.base import ImageInfo, PageRender

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


# ---------- 上下文融合（占位符回填） ----------


def inline_image_text(doc_text: str, images: list[ImageInfo]) -> str | None:
    """把正文中的图片/图表占位符就地替换为 OCR 结果（混合型上下文融合）。

    - 正常图片：替换为引用块 "> 图：…"；图表区为 "> 图表：…"；
    - 被跳过/去重/合并/OCR 失败的图片：移除占位符（内容已由保留者承载）；
    - 正文没有任何占位符时返回 None，调用方回退 build_merged_text 追加文末。
    """
    if not images:
        return None
    if not any(img.marker and img.marker in doc_text for img in images):
        return None

    result = doc_text
    for img in images:
        if not img.marker or img.marker not in result:
            continue
        result = result.replace(img.marker, _inline_replacement(img))
    # 清理占位符移除后留下的连续空行
    return re.sub(r"\n{3,}", "\n\n", result).strip("\n")


def _inline_replacement(img: ImageInfo) -> str:
    body = (img.ocr_text or "").strip()
    if (
        getattr(img, "_skipped", False)
        or getattr(img, "_deduped", False)
        or getattr(img, "_merged", False)
        or not body
    ):
        return ""
    label = "图表" if img.kind == "chart" else "图"
    newline = "\n"
    return f"> {label}：{body.replace(newline, newline + '> ')}"

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
            # 图表区用描述提示词（类型/趋势/数值），图片区维持纯文本转写
            if img.kind == "chart":
                img.ocr_text = asyncio.run(
                    asyncio.wait_for(ocr.describe_chart(img.data), timeout=_OCR_TIMEOUT_SECONDS)
                )
            else:
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


# ---------- 页级 OCR（PDF 损坏页/扫描页兜底） ----------

_PAGE_MARKER_RE = re.compile(r"^--- Page (\d+) ---[ \t]*$", re.MULTILINE)


def replace_page_text(doc_text: str, page_texts: dict[int, str]) -> str:
    """把 `--- Page N ---` 标记页的正文替换为视觉 OCR 转录结果。

    未提供替换的页保持原样；doc_text 没有页标记时原样返回。
    """
    if not page_texts:
        return doc_text
    matches = list(_PAGE_MARKER_RE.finditer(doc_text))
    if not matches:
        return doc_text

    parts: list[str] = [doc_text[: matches[0].start()]]
    for i, match in enumerate(matches):
        page_no = int(match.group(1))
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(doc_text)
        parts.append(match.group(0) + "\n")
        replacement = page_texts.get(page_no)
        if replacement is not None:
            parts.append(replacement.strip() + "\n\n")
        else:
            parts.append(doc_text[match.end() : body_end])
    return "".join(parts)


async def run_page_ocr(
    renders: list[PageRender],
    *,
    concurrency: int = 4,
    timeout_seconds: float | None = None,
) -> tuple[dict[int, str], list[PageRender]]:
    """并发对页面渲染图执行视觉 OCR。

    返回 (成功页的转录结果 {page: markdown}, 失败页列表)。单页失败不影响其他页。
    """
    if not renders:
        return {}, []

    ocr = VisionLLMOCR.from_settings()
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else getattr(settings, "llm_timeout_seconds", 120)
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(render: PageRender) -> tuple[PageRender, str | None]:
        async with semaphore:
            try:
                markdown = await asyncio.wait_for(
                    ocr.extract_markdown(render.data), timeout=timeout
                )
                return render, (markdown or None)
            except Exception:
                return render, None

    pairs = await asyncio.gather(*(_one(render) for render in renders))
    succeeded = {render.page: markdown for render, markdown in pairs if markdown}
    failed = [render for render, markdown in pairs if not markdown]
    return succeeded, failed
