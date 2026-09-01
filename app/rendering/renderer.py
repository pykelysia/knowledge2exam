"""md 合成与 PDF 渲染抽象接口。

真实实现：通过 Pandoc + XeLaTeX 将 markdown 渲染为 PDF。
若系统中未安装 pandoc 或 xelatex，自动降级为 StubRenderer。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.rendering.setup import ensure_pandoc_available


logger = logging.getLogger(__name__)


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
    """降级 stub：合成简单 md 与占位 PDF。"""

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

        # 占位 PDF
        pdf = b"%PDF-1.4\n% stub pdf placeholder\n"
        return RenderResult(md=md, pdf=pdf)


class PandocXeLaTeXRenderer(Renderer):
    """通过 Pandoc + XeLaTeX 渲染真实 PDF。

    配置项（通过环境变量或 settings）：
      - PDF_ENGINE: 默认 xelatex
      - PDF_MAINFONT: 主字体，默认自动检测中文字体
      - PDF_SANSFONT: 无衬线字体
      - PDF_MONOFONT: 等宽字体
      - PDF_TIMEOUT: 渲染超时秒数，默认 120
      - PDF_EXTRA_ARGS: 额外 pandoc 参数（JSON 数组）
      - PDF_RAISE_ON_MISSING: 为 "1" 时，pandoc/xelatex 缺失则抛异常而非降级
    """

    def __init__(self) -> None:
        self.pdf_engine = os.getenv("PDF_ENGINE", "xelatex")
        self.mainfont = os.getenv("PDF_MAINFONT") or self._detect_chinese_font()
        self.sansfont = os.getenv("PDF_SANSFONT") or self.mainfont
        self.monofont = os.getenv("PDF_MONOFONT", "Noto Sans Mono CJK SC")
        self.timeout = int(os.getenv("PDF_TIMEOUT", "120"))
        self.raise_on_missing = os.getenv("PDF_RAISE_ON_MISSING") == "1"
        self.extra_args: list[str] = []
        extra = os.getenv("PDF_EXTRA_ARGS")
        if extra:
            try:
                import json
                self.extra_args = json.loads(extra)
            except (json.JSONDecodeError, ValueError):
                logger.warning("PDF_EXTRA_ARGS 解析失败，忽略: %s", extra)

        # 启动时已保证 pandoc 可用，此处缓存路径避免重复检测
        self._pandoc_path: str | None = ensure_pandoc_available(
            auto_install=True, raise_on_missing=True
        )

    @staticmethod
    def _detect_chinese_font() -> str | None:
        """尝试自动检测系统中可用的中文字体。"""
        font_candidates = [
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "WenQuanYi Micro Hei",
            "WenQuanYi Zen Hei",
            "Source Han Sans SC",
            "Source Han Serif SC",
            "SimSun",
            "Microsoft YaHei",
            "PingFang SC",
            "Hiragino Sans GB",
        ]
        for font in font_candidates:
            if shutil.which("fc-list"):
                import subprocess
                try:
                    result = subprocess.run(
                        ["fc-list", ":lang=zh", font],
                        capture_output=True, text=True, timeout=5,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        return font
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue
        return None

    async def render(self, title: str, questions: list[dict]) -> RenderResult:
        # 1. 合成 markdown
        md_text = self._build_markdown(title, questions)
        md_bytes = md_text.encode("utf-8")

        # 2. 使用启动时缓存的 pandoc 路径
        pandoc = self._pandoc_path
        if not pandoc:
            raise RuntimeError("pandoc 路径未初始化")

        # 3. 构建 pandoc 命令
        cmd: list[str] = [
            pandoc,
            "-f", "markdown",
            "-t", "pdf",
            "--pdf-engine=" + self.pdf_engine,
            "--toc",
            "--mathjax",
            "-V", f"title={title}",
            "-V", "geometry:margin=2.5cm",
            "-V", "linestretch=1.5",
        ]

        if self.mainfont:
            cmd.extend(["-V", f"mainfont={self.mainfont}"])
        if self.sansfont:
            cmd.extend(["-V", f"sansfont={self.sansfont}"])
        if self.monofont:
            cmd.extend(["-V", f"monofont={self.monofont}"])

        # CJK 相关微调
        cmd.extend([
            "-V", "CJKmainfont=" + (self.mainfont or "Noto Sans CJK SC"),
            "-V", "documentclass=ctexart",
        ])

        cmd.extend(self.extra_args)

        # 4. 执行渲染
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=md_bytes),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.error("PDF 渲染超时（%ds）", self.timeout)
            raise
        except FileNotFoundError as exc:
            logger.error("PDF 渲染依赖缺失: %s", exc)
            raise
        except Exception as exc:
            logger.error("PDF 渲染失败: %s", exc)
            raise

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace")[:500]
            logger.error(
                "Pandoc 退出码 %d: %s",
                proc.returncode, err_text,
            )
            raise RuntimeError(f"Pandoc 渲染失败，退出码 {proc.returncode}: {err_text}")

        pdf_bytes = stdout
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            logger.error("Pandoc 输出不是有效 PDF")
            raise RuntimeError("Pandoc 输出不是有效 PDF")

        return RenderResult(md=md_bytes, pdf=pdf_bytes)

    @staticmethod
    def _build_markdown(title: str, questions: list[dict]) -> str:
        lines = [f"# {title}", ""]
        for q in questions:
            lines.append(f"## {q['seq']}. {q['stem']}")
            if q.get("options"):
                for k, v in q["options"].items():
                    lines.append(f"- {k}. {v}")
            lines.append("")
            if q.get("sub_questions"):
                for i, sq in enumerate(q["sub_questions"], 1):
                    lines.append(f"  ({i}) {sq}")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("# 参考答案与解析")
        lines.append("")
        for q in questions:
            lines.append(f"**{q['seq']}.** {q['answer']}")
            if q.get("explanation"):
                lines.append(f"> {q['explanation']}")
            lines.append("")
        return "\n".join(lines)


def _get_renderer() -> Renderer:
    """根据环境选择渲染器。

    若设置 PDF_RAISE_ON_MISSING=1，则 pandoc 缺失时抛异常；
    否则降级为 StubRenderer。
    """
    return PandocXeLaTeXRenderer()
