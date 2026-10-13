"""统一文本切块模块：与格式、来源类型无关的单一路径。

输入为统一文本契约的解析文本：`#` 层级标题行 + 空行分隔的段落
（含表格 Markdown、图片 OCR 回填文本）。算法：

1. 结构分析：`#` 标题行维护层级栈（heading_path），标题不进正文；
2. 段落贪心合并至 chunk_size（预算只约束正文）；
3. 超长段落递归切分：句末标点切句（L2）→ 无标点则硬字符截断（L3）；
4. 所有出块边界统一尾部重叠 overlap 字符（取上块末段尾部）；
5. char_start/char_end 指向该块自有正文（不含重叠前缀）在输入文本中
   的区间，标题行偏移归属其后首个 chunk；
6. embedding_text = heading_path + "\\n" + text，唯一进入嵌入模型的字段。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.core.debug_log import log_step_sync
from app.core.enums import SourceType

# 标题行（Markdown 风格，1-6 级），解析器统一以此契约输出结构
_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# L2 句末标点（连续标点视为一句结束）
_SENTENCE_ENDERS = "。！？；!?．"


@dataclass
class Chunk:
    """切块结果。"""

    text: str
    chunk_index: int = 0
    # 标题路径（如 "第2章 > 2.3 碰撞"），仅元数据与嵌入前缀，不进正文
    heading_path: str | None = None
    # 自有正文在输入文本中的字符区间（不含重叠前缀），[start, end)
    char_start: int | None = None
    char_end: int | None = None
    upload_id: uuid.UUID | None = None
    source_type: SourceType | None = None
    # 进入嵌入模型的文本 = heading_path + "\n" + text，由 chunk() 统一填充
    embedding_text: str | None = None
    # 嵌入向量由解析管线计算后绑定；upsert 依赖此字段，缺失即入库失败
    embedding: list[float] | None = None


@dataclass
class _Unit:
    """待合并的正文单元（段落或其切分片段）。"""

    start: int  # 区间起点（标题后的首个单元继承标题行偏移）
    text_start: int  # 正文首字符偏移
    end: int  # 区间终点（不含）
    text: str
    heading_path: str | None


def _parse_structure(text: str) -> list[_Unit]:
    """扫描全文：标题行维护层级栈并从正文剥离，空行分段落。

    标题行偏移记入其后首个正文单元的 start（归属规则），保证
    chunk 区间可回指"标题 + 正文"的完整片段。
    """
    units: list[_Unit] = []
    heading_stack: list[tuple[int, str]] = []
    pending_start: int | None = None
    para_lines: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal para_lines, pending_start
        if para_lines:
            units.append(
                _Unit(
                    start=pending_start if pending_start is not None else para_lines[0][0],
                    text_start=para_lines[0][0],
                    end=para_lines[-1][0] + len(para_lines[-1][1]),
                    text="\n".join(line for _, line in para_lines).strip(),
                    heading_path=" > ".join(title for _, title in heading_stack) or None,
                )
            )
            pending_start = None
        para_lines = []

    pos = 0
    for line in text.split("\n"):
        line_start = pos
        pos += len(line) + 1
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        match = _HEADING_LINE_RE.match(stripped)
        if match:
            flush()
            level = len(match.group(1))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, match.group(2)))
            if pending_start is None:
                pending_start = line_start
            continue
        lead = len(line) - len(line.lstrip())
        para_lines.append((line_start + lead, stripped))

    flush()
    return units


class Chunker:
    """统一文本切块器：结构优先、大小兜底。"""

    def __init__(self, chunk_size: int = 700, overlap: int = 50) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str) -> list[Chunk]:
        """将统一契约文本切分为多个 chunk。"""
        units = _parse_structure(text)
        pieces: list[_Unit] = []
        for unit in units:
            if len(unit.text) <= self.chunk_size:
                pieces.append(unit)
            else:
                pieces.extend(self._split_oversized(unit))

        chunks: list[Chunk] = []
        current: list[_Unit] = []
        current_len = 0
        overlap_prefix = ""
        for piece in pieces:
            if current and current_len + len(piece.text) > self.chunk_size:
                chunks.append(self._emit(current, overlap_prefix))
                tail = current[-1].text
                overlap_prefix = tail[-self.overlap :] if self.overlap > 0 else ""
                current, current_len = [], 0
            current.append(piece)
            current_len += len(piece.text)
        if current:
            chunks.append(self._emit(current, overlap_prefix))

        for index, chunk in enumerate(chunks):
            chunk.chunk_index = index
            chunk.embedding_text = (
                f"{chunk.heading_path}\n{chunk.text}" if chunk.heading_path else chunk.text
            )

        if chunks:
            log_step_sync(
                job_id="",
                name="chunker.chunk",
                stage="preprocessing",
                input={"text_chars": len(text)},
                output={"chunk_count": len(chunks)},
            )

        return chunks

    @staticmethod
    def _emit(pieces: list[_Unit], overlap_prefix: str) -> Chunk:
        body = "\n\n".join(p.text for p in pieces)
        text = f"{overlap_prefix}\n\n{body}" if overlap_prefix else body
        return Chunk(
            text=text,
            heading_path=pieces[0].heading_path,
            char_start=pieces[0].start,
            char_end=pieces[-1].end,
        )

    def _split_oversized(self, unit: _Unit) -> list[_Unit]:
        """超长段落递归切分：窗口内最右句界切刀，无句界硬截断。"""
        text = unit.text
        cuts = self._cut_points(text)
        pieces: list[_Unit] = []
        start = 0
        n = len(text)
        while n - start > self.chunk_size:
            window_end = start + self.chunk_size
            cut = max((c for c in cuts if start < c <= window_end), default=window_end)
            pieces.append(self._make_piece(unit, start, cut, is_first=start == 0))
            start = cut
        pieces.append(self._make_piece(unit, start, n, is_first=start == 0))
        return pieces

    @staticmethod
    def _make_piece(unit: _Unit, start: int, end: int, *, is_first: bool) -> _Unit:
        raw = unit.text[start:end]
        lead = len(raw) - len(raw.lstrip())
        trail = len(raw) - len(raw.rstrip())
        text_start = unit.text_start + start + lead
        return _Unit(
            # 首个片段继承父单元的标题归属偏移，其余片段从自身正文起算
            start=unit.start if is_first else text_start,
            text_start=text_start,
            end=unit.text_start + end - trail,
            text=raw.strip(),
            heading_path=unit.heading_path,
        )

    @staticmethod
    def _cut_points(text: str) -> list[int]:
        """L2 切点：句末标点串之后、换行串之后（文末不产生）。"""
        cuts: list[int] = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch in _SENTENCE_ENDERS:
                while i < n and text[i] in _SENTENCE_ENDERS:
                    i += 1
                cuts.append(i)
            elif ch == "\n":
                while i < n and text[i] == "\n":
                    i += 1
                if i < n:
                    cuts.append(i)
            else:
                i += 1
        return cuts
