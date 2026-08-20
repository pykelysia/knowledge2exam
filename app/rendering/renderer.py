"""md 合成与 PDF 渲染抽象接口。

首版 stub：合成占位 md 与 PDF（md 为真实文本，PDF 为占位内容）。
真实实现见 tech-selection.md 第 9 节（Pandoc + XeLaTeX）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RenderResult:
    md: bytes
    pdf: bytes | None
    pdf_failed: bool = False


class Renderer:
    """产出抽象接口。"""

    async def render(self, title: str, questions: list[dict]) -> RenderResult:  # pragma: no cover
        raise NotImplementedError


class StubRenderer(Renderer):
    """首版 stub：合成简单 md 与占位 PDF。"""

    async def render(self, title: str, questions: list[dict]) -> RenderResult:
        md_lines = [f"# {title}", ""]
        for q in questions:
            md_lines.append(f"{q['seq']}. {q['stem']}")
            if q.get("options"):
                md_lines.append(
                    "   " + " ".join(f"{k}. {v}" for k, v in q["options"].items())
                )
            md_lines.append("")
        md_lines.append("---")
        md_lines.append("")
        md_lines.append("# 参考答案与解析")
        md_lines.append("")
        for q in questions:
            md_lines.append(f"{q['seq']}. {q['answer']}")
            if q.get("explanation"):
                md_lines.append(f"   {q['explanation']}")
            md_lines.append("")
        md = "\n".join(md_lines).encode("utf-8")

        # 占位 PDF：本阶段不真正渲染，仅返回最小 PDF 内容作为占位
        pdf = b"%PDF-1.4\n% stub pdf placeholder\n"
        return RenderResult(md=md, pdf=pdf)
