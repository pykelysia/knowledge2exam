"""Markdown 合成器。

按 agent-design.md 第 10 节的规范合成试卷 md：
- 题卷部分在前，《参考答案与解析》单独成篇，两部分用 `---` 分隔
- 题号连续，跨题型不重新从 1 开始编号
- plan_item 元信息以 HTML 注释形式嵌入题目上方
- need_explanation=false 时答案区不出现任何解析段落
"""

from __future__ import annotations

import re
from typing import Any


def _plan_comment(plan_item: Any) -> str:
    return (
        f"<!-- plan_item: seq={plan_item.seq} "
        f'knowledge_point="{plan_item.knowledge_point}" '
        f'exam_direction="{plan_item.exam_direction}" '
        f'difficulty="{plan_item.difficulty}" '
        f'reference_source="{plan_item.reference_source or "none"}" -->'
    )


def _choice_block(q: Any, seq: int, show_explanation: bool) -> list[str]:
    lines: list[str] = []
    lines.append(f"{seq}. {q.stem}")
    if q.options:
        for key in ("A", "B", "C", "D"):
            lines.append(f"   {key}. {q.options.get(key, '')}")
    lines.append("")
    if show_explanation and q.explanation:
        lines.append(f"   解析：{q.explanation}")
        lines.append("")
    return lines


def _blank_block(q: Any, seq: int, show_explanation: bool) -> list[str]:
    lines: list[str] = []
    lines.append(f"{seq}. {q.stem}")
    lines.append("")
    if show_explanation and q.explanation:
        lines.append(f"   解析：{q.explanation}")
        lines.append("")
    return lines


def _short_block(q: Any, seq: int, show_explanation: bool) -> list[str]:
    lines: list[str] = []
    lines.append(f"{seq}. {q.stem}")
    if q.sub_questions:
        for idx, sub_q in enumerate(q.sub_questions):
            lines.append(f"   ({idx + 1}) {sub_q}")
    lines.append("")
    if show_explanation and q.explanation:
        lines.append(f"   解析：{q.explanation}")
        lines.append("")
    return lines


def _answer_block(q: Any, seq: int, show_explanation: bool) -> list[str]:
    lines: list[str] = []
    if q.question_type == "choice":
        lines.append(f"{seq}. {q.answer}")
        if show_explanation and q.explanation:
            lines.append(f"   {q.explanation}")
        lines.append("")
    elif q.question_type == "blank":
        lines.append(f"{seq}. {q.answer}")
        if show_explanation and q.explanation:
            lines.append(f"   解析：{q.explanation}")
        lines.append("")
    elif q.question_type == "short_answer":
        if q.sub_questions and q.sub_answers:
            lines.append(f"{seq}. ")
            for idx, sub_a in enumerate(q.sub_answers):
                lines.append(f"   ({idx + 1}) {sub_a}")
            lines.append("")
            if show_explanation and q.explanation:
                lines.append(f"   解析：{q.explanation}")
                lines.append("")
        else:
            lines.append(f"{seq}. {q.answer}")
            if show_explanation and q.explanation:
                lines.append(f"   解析：{q.explanation}")
            lines.append("")
    return lines


def _clean_literal_escapes(md_text: str) -> str:
    """清理 markdown 中的字面量转义序列（如 `\\n`、`\\t`），
    避免 Pandoc 渲染为 PDF 时生成无效 LaTeX。

    LLM 有时会生成包含字面量 ``\\n``/``\\t`` 的代码片段，而不是真正的换行/制表符。
    此函数会：
    - 识别围栏代码块（fenced code blocks）内的字面量转义序列，并将其替换为实际字符；
    - 同时清理围栏代码块标记周围多余的空白，确保 Pandoc 正确识别代码块。
    """
    # 匹配模式：字面量 \n + ```lang + 字面量 \n + 代码内容 + 字面量 \n + ```
    # 使用 DOTALL 使 . 能匹配换行
    fence_pattern = re.compile(
        r"(?:\\n|\n)(```(?:[a-zA-Z0-9_+-]*)(?:\\n|\n))(.*?)((?:\\n|\n)```)",
        re.DOTALL,
    )

    def _replace_escapes_in_code(match: re.Match) -> str:
        opening = match.group(1)  # 包含 ```lang\n 或 ```lang
        code = match.group(2)
        closing = match.group(3)  # 包含 \n``` 或 \n```
        # 将围栏标记中的字面量 \n 转换为实际换行
        opening = opening.replace("\\n", "\n")
        closing = closing.replace("\\n", "\n")
        # 将代码内的字面量转义序列替换为实际字符
        code = code.replace("\\n", "\n")
        code = code.replace("\\t", "\t")
        code = code.replace('\\"', '"')
        code = code.replace("\\'", "'")
        # 确保代码块前后都有实际换行
        result = f"\n{opening}{code}{closing}"
        return result

    md_text = fence_pattern.sub(_replace_escapes_in_code, md_text)

    # 清理单独出现在行尾的 ``\n``（非代码块内），避免被 Pandoc 解释为 LaTeX 命令
    md_text = re.sub(r"\\n", "\\\\n", md_text)
    md_text = re.sub(r"\\t", "\\\\t", md_text)

    return md_text


def build_markdown(
    title: str,
    questions: list[tuple[Any, Any]],
    need_explanation: bool,
) -> str:
    """合成完整的试卷 Markdown。

    Args:
        title: 试卷标题
        questions: [(plan_item, question), ...] 按 seq 排序
        need_explanation: 是否生成解析

    Returns:
        合成后的 Markdown 文本
    """
    lines: list[str] = [f"# {title}", ""]

    # 按题型分组
    choice_items: list[tuple[Any, Any]] = []
    blank_items: list[tuple[Any, Any]] = []
    short_items: list[tuple[Any, Any]] = []

    for plan, q in questions:
        if q.question_type == "choice":
            choice_items.append((plan, q))
        elif q.question_type == "blank":
            blank_items.append((plan, q))
        elif q.question_type == "short_answer":
            short_items.append((plan, q))

    seq = 0

    if choice_items:
        lines.append("## 一、选择题")
        lines.append("")
        for plan, q in choice_items:
            seq += 1
            lines.append(_plan_comment(plan))
            lines.extend(_choice_block(q, seq, need_explanation))

    if blank_items:
        lines.append("## 二、填空题")
        lines.append("")
        for plan, q in blank_items:
            seq += 1
            lines.append(_plan_comment(plan))
            lines.extend(_blank_block(q, seq, need_explanation))

    if short_items:
        lines.append("## 三、简答题")
        lines.append("")
        for plan, q in short_items:
            seq += 1
            lines.append(_plan_comment(plan))
            lines.extend(_short_block(q, seq, need_explanation))

    # 分隔线
    lines.append("---")
    lines.append("")
    lines.append("# 参考答案与解析")
    lines.append("")

    seq = 0
    for _plan, q in choice_items:
        seq += 1
        lines.extend(_answer_block(q, seq, need_explanation))

    for _plan, q in blank_items:
        seq += 1
        lines.extend(_answer_block(q, seq, need_explanation))

    for _plan, q in short_items:
        seq += 1
        lines.extend(_answer_block(q, seq, need_explanation))

    md_text = "\n".join(lines)
    return _clean_literal_escapes(md_text)
