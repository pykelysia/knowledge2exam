"""Agent 提示词：加载与渲染。

模板位于 prompts/ 目录，占位符写法为 `{{ name }}`；
渲染时逐个替换，模板中的 JSON 花括号不受影响。
"""

from __future__ import annotations

import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def load_prompt(name: str) -> str:
    """读取 prompts/ 下的模板文件（如 "system"、"intent"）。"""
    path = (_PROMPTS_DIR / f"{name}.md").resolve()
    # is_relative_to 按路径分量比较，避免 startswith 的前缀误判
    # （如 ../prompts_backup/x 也能通过 str.startswith(prompts)）
    if not path.is_relative_to(_PROMPTS_DIR) or not path.is_file():
        raise FileNotFoundError(f"提示词模板不存在: {name}")
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, **values: str) -> str:
    """替换模板中的 `{{ name }}` 占位符。"""

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"提示词模板缺少占位符取值: {key}")
        return values[key]

    return _PLACEHOLDER_RE.sub(_sub, template)
