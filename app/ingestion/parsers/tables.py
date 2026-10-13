"""表格行数据 → Markdown 的共享工具（docx/pptx 解析器共用）。

pymupdf 的 Table.to_markdown 绑定其自有表格检测产物，无法复用；
此模块只依赖行列数据本身。
"""

from __future__ import annotations


def rows_to_markdown(rows: list[list[str]]) -> str:
    """行数据转 Markdown 表格，首行为表头；空输入返回空串。

    全空行剔除；行长不齐时右补空单元格；单元格内的竖线转义、
    换行压平，避免破坏表格结构。
    """
    cleaned = [
        [_clean_cell(cell) for cell in row]
        for row in rows
        if any(cell.strip() for cell in row)
    ]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    padded = [row + [""] * (width - len(row)) for row in cleaned]
    header, body = padded[0], padded[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _clean_cell(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ").strip()
