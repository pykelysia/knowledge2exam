"""PDF 版面分析：文档类型检测 + 区域切分。

实现 docs/pdf_analyzer.md 的三分类路由：
- pure_text 纯文本：文本层可信且无显著图片/表格/图表 → 直接结构化抽取；
- scanned   扫描件：页面以图片为主、文本层缺失或不可信 → 栅格化 + 视觉 OCR；
- mixed     混合型：文本与表格/图表/图片并存 → 按区域分链路解析。

classify_document 是纯函数便于单测；collect_page_contents 单遍收集版面
数据（文本块/表格/图片/矢量图表簇），解析器据此组装正文，避免二次扫描。
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.ingestion.quality import page_needs_ocr

logger = logging.getLogger(__name__)

# ---- 类型判定阈值 ----
# 无有效文本层且图片覆盖率 ≥ 此值 → 扫描页
_SCANNED_PAGE_COVERAGE = 0.5
# 健康页整页大图 + 足量文本：典型"扫描后带隐藏文本层"，以文本层为准
_FULL_PAGE_IMAGE_COVERAGE = 0.7
_FULL_PAGE_MIN_TEXT_CHARS = 120
# 页面图片覆盖率 ≥ 此值视为"有显著视觉内容"（影响混合型判定）
_SIGNIFICANT_VISUAL_COVERAGE = 0.15
# 扫描页占比 ≥ 此值 → 整本判定为扫描件
_SCANNED_DOC_RATIO = 0.9
# 页面文本层有效字符数下限（低于视为无文本层）
_MIN_TEXT_CHARS = 32
# 矢量图簇面积占页面比例下限（低于视为装饰线框）
_CHART_MIN_AREA_RATIO = 0.04
# 文本块被表格/图表区域覆盖的比例达到此值时从正文剔除
# （其内容由表格 Markdown / 图表转录承载，避免重复）
_REGION_COVER_RATIO = 0.6

# ---- 标题启发式 ----
_HEADING_SIZE_RATIO = 1.15   # 相对正文字号的倍数下限
_HEADING_MAX_CHARS = 60      # 标题文本长度上限
_TOC_MATCH_TOLERANCE = 25.0  # TOC 目标 y 与文本块 y0 的匹配容差（pt）


class PdfType(StrEnum):
    """PDF 文档类型（docs/pdf_analyzer.md 三分类）。"""

    PURE_TEXT = "pure_text"
    SCANNED = "scanned"
    MIXED = "mixed"


@dataclass
class PageStats:
    """单页体检与视觉元素统计（类型判定的输入）。"""

    page: int  # 1-based
    text_chars: int
    ocr_reason: str  # "" | "no_text" | "garbled"（复用页级体检语义）
    image_coverage: float = 0.0  # 图片面积占页面比例，0~1
    has_tables: bool = False
    has_charts: bool = False

    @property
    def healthy(self) -> bool:
        return self.ocr_reason == ""

    @property
    def is_scanned(self) -> bool:
        return not self.healthy and self.image_coverage >= _SCANNED_PAGE_COVERAGE

    @property
    def has_visuals(self) -> bool:
        return (
            self.image_coverage >= _SIGNIFICANT_VISUAL_COVERAGE
            or self.has_tables
            or self.has_charts
        )


@dataclass
class TextBlock:
    """文本块（段落级），bbox 为 (x0, y0, x1, y1)。"""

    bbox: tuple[float, float, float, float]
    text: str
    max_size: float  # 块内最大字号（标题启发式用）


@dataclass
class TableInfo:
    """表格区域：PyMuPDF 检出的规则表格及其 Markdown 转写。"""

    bbox: tuple[float, float, float, float]
    markdown: str


@dataclass
class ImageRef:
    """图片区域：bbox 与内嵌图片原始字节。"""

    bbox: tuple[float, float, float, float]
    data: bytes


@dataclass
class ChartRef:
    """图表区域：矢量绘图聚类出的 bbox（渲染与转录由解析器编排）。"""

    bbox: tuple[float, float, float, float]


@dataclass
class PageContent:
    """单页版面内容（收集一次，分类与组装复用）。"""

    stats: PageStats
    blocks: list[TextBlock] = field(default_factory=list)
    tables: list[TableInfo] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    charts: list[ChartRef] = field(default_factory=list)


def overlap_ratio(inner: tuple[float, float, float, float],
                  outer: tuple[float, float, float, float]) -> float:
    """inner 与 outer 相交面积占 inner 面积的比例。"""
    ix0, iy0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix1, iy1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inner_area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    if inner_area <= 0:
        return 0.0
    return ((ix1 - ix0) * (iy1 - iy0)) / inner_area


def is_region_covered(bbox: tuple[float, float, float, float],
                      regions: Sequence[tuple[float, float, float, float]]) -> bool:
    """bbox 是否被 regions 中任一区域覆盖到剔除阈值。"""
    return any(overlap_ratio(bbox, region) >= _REGION_COVER_RATIO for region in regions)


def skip_embedded_images(stats: PageStats) -> bool:
    """是否跳过该页内嵌图片提取（避免与整页内容重复）。

    - 扫描页：内容由整页渲染 + OCR 承载；
    - 健康页 + 整页大图 + 足量文本：典型"扫描后带隐藏文本层"，文本层即权威内容。
    """
    if stats.is_scanned:
        return True
    return (
        stats.healthy
        and stats.image_coverage >= _FULL_PAGE_IMAGE_COVERAGE
        and stats.text_chars >= _FULL_PAGE_MIN_TEXT_CHARS
    )


def classify_document(pages: Sequence[PageStats]) -> PdfType:
    """按页面统计判定文档类型（纯函数）。

    - 绝大多数页为扫描页 → 扫描件；
    - 存在显著视觉内容（图片/表格/图表）→ 混合型；
    - 其余 → 纯文本（个别乱码页由页级体检兜底，不影响文档级类型）。
    """
    total = len(pages)
    if total == 0:
        return PdfType.PURE_TEXT
    scanned = sum(1 for p in pages if p.is_scanned)
    if scanned / total >= _SCANNED_DOC_RATIO:
        return PdfType.SCANNED
    if any(p.has_visuals for p in pages):
        return PdfType.MIXED
    return PdfType.PURE_TEXT


def collect_page_contents(doc) -> list[PageContent]:
    """单遍收集每页版面内容：文本块、表格、图片、矢量图表簇。"""
    contents: list[PageContent] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        try:
            raw = page.get_text("dict")
        except Exception:
            logger.debug("page %s get_text 失败", page_num + 1, exc_info=True)
            raw = {"blocks": []}

        blocks: list[TextBlock] = []
        image_bboxes: list[tuple[float, float, float, float]] = []
        image_datas: list[bytes | None] = []
        for obj in raw.get("blocks", []):
            bbox = obj.get("bbox")
            if not bbox:
                continue
            if obj.get("type") == 0:
                block = _build_text_block(obj)
                if block is not None:
                    blocks.append(block)
            elif obj.get("type") == 1:
                image_bboxes.append(tuple(bbox))
                image_datas.append(obj.get("image"))

        tables = _collect_tables(page)
        charts = _collect_charts(page, tables, image_bboxes)

        page_area = abs(page.rect) if page.rect else 0.0
        coverage = _image_coverage(image_bboxes, page_area)
        joined = "\n".join(block.text for block in blocks)
        _, reason = page_needs_ocr(joined)
        stats = PageStats(
            page=page_num + 1,
            text_chars=len(joined.strip()),
            ocr_reason=reason,
            image_coverage=coverage,
            has_tables=bool(tables),
            has_charts=bool(charts),
        )

        # 无有效文本层的扫描页 / 带隐藏文本层的整页大图：跳过内嵌图片提取
        images: list[ImageRef] = []
        if not skip_embedded_images(stats):
            for bbox, data in zip(image_bboxes, image_datas, strict=True):
                if data:
                    images.append(ImageRef(bbox=bbox, data=data))

        contents.append(
            PageContent(
                stats=stats,
                blocks=blocks,
                tables=tables,
                images=images,
                charts=charts,
            )
        )
    return contents


# ---- 标题结构 ----


@dataclass
class _HeadingEntry:
    page: int
    y: float | None
    level: int
    title: str
    matched: bool = False


class HeadingMap:
    """标题层级索引：TOC 命中按 y 匹配文本块，未命中条目独立插入。"""

    def __init__(self, entries: list[_HeadingEntry]) -> None:
        self._entries = entries

    def level_for(self, page: int, y0: float) -> int | None:
        """返回 (page, y0) 处文本块的标题层级；非标题返回 None。"""
        for entry in self._entries:
            if (
                entry.page == page
                and entry.y is not None
                and abs(entry.y - y0) <= _TOC_MATCH_TOLERANCE
            ):
                entry.matched = True
                return entry.level
        return None

    def take_pending(self, page: int) -> list[tuple[float | None, int, str]]:
        """取该页未命中任何文本块的标题条目（y, level, title），取后即清。"""
        pending = [
            (entry.y, entry.level, entry.title)
            for entry in self._entries
            if entry.page == page and not entry.matched
        ]
        for entry in self._entries:
            if entry.page == page:
                entry.matched = True
        return pending


def build_heading_map(doc, contents: Sequence[PageContent]) -> HeadingMap:
    """构建标题索引：优先 PDF 目录（TOC），无目录时退字体大小启发式。"""
    entries: list[_HeadingEntry] = []
    try:
        toc = doc.get_toc(simple=False) or []
    except Exception:
        toc = []
    for item in toc:
        entry = _toc_entry(item, page_count=len(contents))
        if entry is not None:
            entries.append(entry)
    if entries:
        return HeadingMap(entries)
    return HeadingMap(_heuristic_headings(contents))


def _toc_entry(item, *, page_count: int) -> _HeadingEntry | None:
    """解析单条 TOC（兼容 simple 的 [lvl, title, page] 与完整 dict）。"""
    if isinstance(item, dict):
        page = item.get("page")
        level = item.get("level") or 1
        title = str(item.get("title") or "")
        dest = item.get("dest") or {}
        to = dest.get("to") if isinstance(dest, dict) else None
        y = float(to.y) if to is not None and hasattr(to, "y") else None
    elif isinstance(item, (list, tuple)) and len(item) >= 3:
        level, title, page = item[0], str(item[1]), item[2]
        y = None
    else:
        return None
    if not isinstance(page, int) or not (1 <= page <= page_count):
        return None
    try:
        level = max(1, min(int(level), 4))
    except (TypeError, ValueError):
        level = 1
    return _HeadingEntry(page=page, y=y, level=level, title=title)


def _heuristic_headings(contents: Sequence[PageContent]) -> list[_HeadingEntry]:
    """字体大小启发式：显著大于正文字号且足够短的块视为标题。"""
    body_size = _body_font_size(contents)
    if body_size <= 0:
        return []

    def is_heading(block: TextBlock) -> bool:
        return (
            block.max_size >= body_size * _HEADING_SIZE_RATIO
            and len(block.text.strip()) <= _HEADING_MAX_CHARS
        )

    sizes: list[float] = []
    for content in contents:
        for block in content.blocks:
            if is_heading(block):
                size = round(block.max_size, 1)
                if size not in sizes:
                    sizes.append(size)
    sizes.sort(reverse=True)
    # 相近字号归为同一层级
    levels: list[float] = []
    for size in sizes:
        if not levels or abs(levels[-1] - size) > 0.5:
            levels.append(size)
    if not levels:
        return []
    rank = {size: min(index + 1, 4) for index, size in enumerate(levels)}

    entries: list[_HeadingEntry] = []
    for content in contents:
        for block in content.blocks:
            if is_heading(block):
                level = rank.get(round(block.max_size, 1))
                if level:
                    entries.append(
                        _HeadingEntry(
                            page=content.stats.page,
                            y=block.bbox[1],
                            level=level,
                            title=block.text.strip(),
                        )
                    )
    return entries


def _body_font_size(contents: Sequence[PageContent]) -> float:
    """正文字号：按块文本长度加权的最常见最大字号。"""
    counter: Counter[float] = Counter()
    for content in contents:
        for block in content.blocks:
            if block.text.strip():
                counter[round(block.max_size, 1)] += len(block.text)
    if not counter:
        return 0.0
    return counter.most_common(1)[0][0]


# ---- 收集辅助 ----


def _build_text_block(obj: dict) -> TextBlock | None:
    """把 get_text("dict") 的文本块转为段落级 TextBlock。"""
    lines: list[str] = []
    max_size = 0.0
    for line in obj.get("lines", []):
        spans = line.get("spans") or []
        line_text = "".join(span.get("text", "") for span in spans)
        for span in spans:
            try:
                max_size = max(max_size, float(span.get("size") or 0))
            except (TypeError, ValueError):
                continue
        if line_text:
            lines.append(line_text)
    text = _join_lines(lines)
    if not text.strip():
        return None
    bbox = obj.get("bbox")
    if not bbox:
        return None
    return TextBlock(bbox=tuple(bbox), text=text, max_size=max_size)


def _join_lines(lines: list[str]) -> str:
    """块内多行合并为段落：西文词界补空格，CJK 直接连接。"""
    out = ""
    for line in lines:
        if out and _needs_space(out[-1], line[0]):
            out += " "
        out += line
    return out


def _needs_space(a: str, b: str) -> bool:
    """前一字符与后一字符都是 ASCII 字母数字时补空格（英文断行）。"""
    return a.isascii() and a.isalnum() and b.isascii() and b.isalnum()


def _collect_tables(page) -> list[TableInfo]:
    """检出页面上的规则表格并转 Markdown；失败按无表格处理。"""
    tables: list[TableInfo] = []
    try:
        finder = page.find_tables()
        for table in finder.tables:
            try:
                markdown = table.to_markdown() or ""
            except Exception:
                markdown = ""
            if markdown.strip():
                tables.append(TableInfo(bbox=tuple(table.bbox), markdown=markdown))
    except Exception:
        logger.debug("page %s find_tables 失败", page.number + 1, exc_info=True)
    return tables


def _collect_charts(page, tables: list[TableInfo],
                    image_bboxes: Sequence[tuple[float, float, float, float]]) -> list[ChartRef]:
    """矢量绘图聚类出图表候选区，剔除表格线框、位图区域与过小簇。"""
    charts: list[ChartRef] = []
    try:
        clusters = page.cluster_drawings()
    except Exception:
        return charts
    page_area = abs(page.rect) if page.rect else 0.0
    if page_area <= 0:
        return charts
    for cluster in clusters:
        bbox = tuple(cluster)
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if area <= 0 or area / page_area < _CHART_MIN_AREA_RATIO:
            continue
        if any(overlap_ratio(bbox, table.bbox) >= _REGION_COVER_RATIO for table in tables):
            continue
        if any(overlap_ratio(bbox, img) >= _REGION_COVER_RATIO for img in image_bboxes):
            continue
        charts.append(ChartRef(bbox=bbox))
    return charts


def _image_coverage(image_bboxes: Sequence[tuple[float, float, float, float]],
                    page_area: float) -> float:
    if page_area <= 0 or not image_bboxes:
        return 0.0
    total = sum(
        max(0.0, (b[2] - b[0])) * max(0.0, (b[3] - b[1])) for b in image_bboxes
    )
    return min(1.0, total / page_area)
