"""Markdown 合成器。

按 agent-design.md 第 10 节的规范合成试卷 md：
- 题卷部分在前，《参考答案与解析》单独成篇，两部分用 `---` 分隔
- 题号连续，跨题型不重新从 1 开始编号
- plan_item 元信息以 HTML 注释形式嵌入题目上方
- need_explanation=false 时答案区不出现任何解析段落
"""

from __future__ import annotations

from app.schemas.job import Stage


def _plan_comment(plan_item: Any) -> str:
    return (
        f'<!-- plan_item: seq={plan_item.seq} '
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
    for plan, q in choice_items:
        seq += 1
        lines.extend(_answer_block(q, seq, need_explanation))

    for plan, q in blank_items:
        seq += 1
        lines.extend(_answer_block(q, seq, need_explanation))

    for plan, q in short_items:
        seq += 1
        lines.extend(_answer_block(q, seq, need_explanation))

    return "\n".join(lines)
