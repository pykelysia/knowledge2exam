"""提示词模板加载器。"""

from __future__ import annotations

from pathlib import Path

from app.config import settings

_PROMPTS_DIR = Path(__file__).parent

# 预置模板文件映射
_TEMPLATE_FILES: dict[str, str] = {
    "planner": "planner.md",
    "writers": "writers.md",
    "reviewer": "reviewer.md",
    "compressor": "compressor.md",
}


def load_prompt(name: str) -> str:
    """从 prompts/ 目录加载模板文件内容。"""
    filename = _TEMPLATE_FILES.get(name)
    if not filename:
        raise KeyError(f"未知提示词模板: {name}")
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


__all__ = ["load_prompt"]
