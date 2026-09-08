"""schemas 模块单元测试：Pydantic 校验器各分支 + 钩子异常隔离。"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.agents.schemas import AgentHooks, ExamQuestion, TodoItem


def choice_question(**overrides: object) -> dict:
    data: dict = {
        "seq": 1,
        "question_type": "choice",
        "stem": "下列说法正确的是？",
        "options": {"A": "对", "B": "错", "C": "都不对", "D": "全对"},
        "answer": "A",
    }
    data.update(overrides)
    return data


class TestExamQuestion:
    def test_valid_choice(self) -> None:
        q = ExamQuestion.model_validate(choice_question())
        assert q.options is not None and set(q.options) == set("ABCD")

    def test_valid_blank(self) -> None:
        q = ExamQuestion.model_validate(
            {"seq": 2, "question_type": "blank", "stem": "1+1=______", "answer": "2"}
        )
        assert q.options is None

    def test_valid_short_answer_with_subs(self) -> None:
        q = ExamQuestion.model_validate(
            {
                "seq": 3,
                "question_type": "short_answer",
                "stem": "证明……",
                "answer": "见解析",
                "sub_questions": ["(1) 求……", "(2) 证……"],
                "sub_answers": ["答一", "答二"],
            }
        )
        assert len(q.sub_questions) == len(q.sub_answers) == 2

    def test_choice_missing_option(self) -> None:
        data = choice_question(options={"A": "对", "B": "错", "C": "都不对"})
        with pytest.raises(ValidationError, match="缺少选项"):
            ExamQuestion.model_validate(data)

    def test_choice_extra_option(self) -> None:
        data = choice_question(
            options={"A": "a", "B": "b", "C": "c", "D": "d", "E": "e"}
        )
        with pytest.raises(ValidationError, match="多余选项"):
            ExamQuestion.model_validate(data)

    def test_choice_bad_answer(self) -> None:
        with pytest.raises(ValidationError, match="answer 必须为"):
            ExamQuestion.model_validate(choice_question(answer="E"))

    def test_non_choice_with_options_rejected(self) -> None:
        data = choice_question(
            question_type="blank",
            options={"A": "a", "B": "b", "C": "c", "D": "d"},
        )
        with pytest.raises(ValidationError, match="不应携带 options"):
            ExamQuestion.model_validate(data)

    def test_bad_question_type(self) -> None:
        with pytest.raises(ValidationError, match="question_type"):
            ExamQuestion.model_validate(choice_question(question_type="essay"))

    def test_sub_only_on_short_answer(self) -> None:
        data = choice_question(
            sub_questions=["x"], sub_answers=["y"]
        )
        with pytest.raises(ValidationError, match="只有简答题"):
            ExamQuestion.model_validate(data)

    def test_sub_without_answers_rejected(self) -> None:
        with pytest.raises(ValidationError, match="同时提供或同时省略"):
            ExamQuestion.model_validate(
                {
                    "seq": 3,
                    "question_type": "short_answer",
                    "stem": "证明",
                    "answer": "见解析",
                    "sub_questions": ["(1)"],
                }
            )

    def test_sub_length_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不一致"):
            ExamQuestion.model_validate(
                {
                    "seq": 3,
                    "question_type": "short_answer",
                    "stem": "证明",
                    "answer": "见解析",
                    "sub_questions": ["(1)", "(2)"],
                    "sub_answers": ["答"],
                }
            )

    def test_empty_stem_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExamQuestion.model_validate(choice_question(stem=""))


class TestTodoItem:
    def test_valid_defaults(self) -> None:
        t = TodoItem(seq=1, question_type="choice", knowledge_point="导数")
        assert t.difficulty == "medium" and t.status == "pending"

    @pytest.mark.parametrize(
        ("field", "value"),
        [("question_type", "essay"), ("difficulty", "insane"), ("status", "done")],
    )
    def test_bad_enum_rejected(self, field: str, value: str) -> None:
        kwargs: dict = {"question_type": "choice", "knowledge_point": "k"}
        kwargs[field] = value
        with pytest.raises(ValidationError):
            TodoItem(seq=1, **kwargs)

    def test_seq_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            TodoItem(seq=0, question_type="choice", knowledge_point="k")


class TestAgentHooks:
    async def test_none_hooks_are_noop(self) -> None:
        hooks = AgentHooks()
        await hooks.fire_plan_ready([])  # 不抛异常即可

    async def test_hook_exception_is_swallowed(self, caplog: pytest.LogCaptureFixture) -> None:
        async def boom(todos: list) -> None:
            raise RuntimeError("hook 炸了")

        hooks = AgentHooks(on_plan_ready=boom)
        with caplog.at_level(logging.ERROR):
            await hooks.fire_plan_ready([])
        assert any("钩子执行失败" in r.message for r in caplog.records)
