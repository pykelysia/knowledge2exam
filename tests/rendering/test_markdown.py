"""markdown 合成器单元测试：TodoItem/dict reference_source 兼容与解析开关。"""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.schemas import ExamQuestion, TodoItem
from app.rendering.markdown import build_markdown


def make_question(seq: int, **overrides: object) -> ExamQuestion:
    data: dict = {
        "seq": seq,
        "question_type": "choice",
        "stem": f"第 {seq} 题题干",
        "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "answer": "A",
        "explanation": "因为所以",
    }
    data.update(overrides)
    return ExamQuestion.model_validate(data)


class TestBuildMarkdown:
    def test_todoitem_without_reference_source(self) -> None:
        """TodoItem 没有 reference_source 字段：注释渲染为 none，不抛错。"""
        plan = TodoItem(seq=1, question_type="choice", knowledge_point="导数")
        q = make_question(1)

        md = build_markdown("试卷（60 分钟）", [(plan, q)], need_explanation=True)

        assert 'reference_source="none"' in md
        assert "## 一、选择题" in md
        assert "# 试卷（60 分钟）" in md
        assert "# 参考答案与解析" in md

    def test_dict_reference_source_rendered_as_json(self) -> None:
        """DB PlanItem 风格的 dict reference_source 渲染为 JSON 串而非 Python repr。"""
        plan = SimpleNamespace(
            seq=1,
            question_type="choice",
            knowledge_point="导数",
            exam_direction="单调性判定",
            difficulty="medium",
            reference_source={"page": 3, "file": "past_paper_01.md"},
        )
        q = make_question(1)

        md = build_markdown("试卷", [(plan, q)], need_explanation=False)

        assert '{"page": 3, "file": "past_paper_01.md"}' in md
        assert "SimpleNamespace" not in md

    def test_explanation_hidden_when_disabled(self) -> None:
        """need_explanation=False：题面与答案区都不出现解析。"""
        plan = TodoItem(seq=1, question_type="choice", knowledge_point="导数")
        q = make_question(1, explanation="因为所以")

        md = build_markdown("试卷", [(plan, q)], need_explanation=False)

        assert "因为所以" not in md
        assert "解析：" not in md  # 「参考答案与解析」标题除外，不能断言裸词
