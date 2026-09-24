"""preference 模块单元测试：结构化提炼与原文兜底（fake 模型，不触网络）。"""

from __future__ import annotations

import uuid

import pytest

from app.agents.preference import (
    MAX_PREFERENCE_CHARS,
    PreferenceDecision,
    consolidate_preference,
    verbatim_preference,
)


class _FakeStructuredRunnable:
    def __init__(self, output: object, prompts: list[str]) -> None:
        self._output = output
        self._prompts = prompts

    async def ainvoke(self, prompt: str) -> object:
        self._prompts.append(prompt)
        return self._output


class FakeModel:
    """最小 BaseChatModel 替身：with_structured_output 回放固定输出。"""

    def __init__(self, output: object) -> None:
        self._output = output
        self.prompts: list[str] = []

    def with_structured_output(self, schema: type) -> _FakeStructuredRunnable:
        assert schema is PreferenceDecision
        return _FakeStructuredRunnable(self._output, self.prompts)


def _existing() -> list[tuple[uuid.UUID, str]]:
    return [(uuid.uuid4(), "题目应有综合性"), (uuid.uuid4(), "解析需含易错点")]


class TestConsolidatePreference:
    async def test_returns_decision_and_renders_prompt(self) -> None:
        decision = PreferenceDecision(action="add", content="新偏好")
        model = FakeModel(decision)

        result = await consolidate_preference(
            model,
            feedback="这道题太简单",
            selection_text="1+1=？",
            existing=_existing(),
        )

        assert result is decision
        prompt = model.prompts[0]
        assert "这道题太简单" in prompt
        assert "1+1=？" in prompt
        assert "题目应有综合性" in prompt

    async def test_empty_selection_renders_placeholder(self) -> None:
        model = FakeModel(PreferenceDecision(action="noop"))
        await consolidate_preference(model, feedback="f", selection_text="", existing=[])
        assert "（未划选）" in model.prompts[0]

    async def test_content_truncated(self) -> None:
        long_content = "长" * (MAX_PREFERENCE_CHARS + 50)
        model = FakeModel(PreferenceDecision(action="add", content=long_content))

        result = await consolidate_preference(
            model, feedback="f", selection_text="s", existing=[]
        )

        assert len(result.content) == MAX_PREFERENCE_CHARS

    async def test_bad_output_type_raises(self) -> None:
        model = FakeModel("not-a-decision")
        with pytest.raises(TypeError, match="输出类型异常"):
            await consolidate_preference(model, feedback="f", selection_text="s", existing=[])


class TestVerbatimFallback:
    def test_format(self) -> None:
        text = verbatim_preference("  这道题太简单  ")
        assert text == "用户反馈（原文沉淀）：这道题太简单"

    def test_truncated(self) -> None:
        text = verbatim_preference("长" * (MAX_PREFERENCE_CHARS + 10))
        assert len(text) == MAX_PREFERENCE_CHARS
