"""schemas 模块单元测试：Pydantic 校验器各分支 + 钩子异常隔离。"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.agents.schemas import (
    AgentHooks,
    RevisionDirective,
    RevisionRound,
    SelectionAnchor,
    TodoItem,
)


def revision_payload(**overrides: object) -> dict:
    data: dict = {
        "selection": {"text": "1+1=？", "before": "选择题", "after": "A. 1"},
        "feedback": "加大难度",
    }
    data.update(overrides)
    return data


class TestRevisionDirective:
    def test_valid_directive(self) -> None:
        d = RevisionDirective.model_validate(revision_payload())
        assert d.selection.text == "1+1=？"
        assert d.feedback == "加大难度"
        assert d.history == []

    def test_history_rounds_parsed(self) -> None:
        d = RevisionDirective.model_validate(
            revision_payload(
                history=[
                    {
                        "round_no": 1,
                        "selection": {"text": "2+2=？"},
                        "feedback": "第一轮",
                        "summary": "已修改",
                    },
                    {"round_no": 2, "feedback": "第二轮（未划选）"},
                ]
            )
        )
        assert [r.round_no for r in d.history] == [1, 2]
        assert d.history[0].selection is not None
        assert d.history[1].selection is None

    def test_empty_selection_text_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SelectionAnchor(text="")

    def test_empty_feedback_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RevisionDirective.model_validate(revision_payload(feedback=""))

    def test_selection_defaults(self) -> None:
        sel = SelectionAnchor(text="锚点")
        assert sel.before == "" and sel.after == ""

    def test_bad_round_no_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RevisionRound(round_no=0, feedback="x")


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
