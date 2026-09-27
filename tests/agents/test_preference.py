"""preference 模块单元测试：结构化提炼与原文兜底（fake 模型，不触网络）。"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.agents.preference import (
    MAX_PREFERENCE_CHARS,
    PreferenceDecision,
    PreferenceExtractionError,
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

    def with_structured_output(self, schema: type, **kwargs: Any) -> _FakeStructuredRunnable:
        assert schema is PreferenceDecision
        return _FakeStructuredRunnable(self._output, self.prompts)


class _FakeRawRunnable:
    """with_structured_output(include_raw=True) 风格的 stub：按序回放响应。"""

    def __init__(self, responses: list[dict[str, Any]], prompts: list[str]) -> None:
        self._responses = responses
        self._prompts = prompts

    async def ainvoke(self, prompt: str) -> dict[str, Any]:
        self._prompts.append(prompt)
        return self._responses.pop(0)


class FakeRawModel:
    """回放 include_raw 字典的假模型，用于解析失败后的恢复路径。"""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.prompts: list[str] = []
        self._responses = responses

    def with_structured_output(self, schema: type, **kwargs: Any) -> _FakeRawRunnable:
        assert schema is PreferenceDecision
        return _FakeRawRunnable(self._responses, self.prompts)


def _raw(content: Any) -> dict[str, Any]:
    """构造解析失败的 include_raw 字典：parsed 为空，raw 携带模型原文。"""
    return {
        "raw": AIMessage(content=content),
        "parsed": None,
        "parsing_error": ValueError("Invalid JSON: expected value at line 1 column 1"),
    }


def _parsed(decision: PreferenceDecision) -> dict[str, Any]:
    """构造解析成功的 include_raw 字典。"""
    return {
        "raw": AIMessage(content="（结构化输出成功）"),
        "parsed": decision,
        "parsing_error": None,
    }


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
        with pytest.raises(PreferenceExtractionError, match="偏好提炼失败"):
            await consolidate_preference(model, feedback="f", selection_text="s", existing=[])

    async def test_parse_error_recovers_fenced_json(self) -> None:
        payload = json.dumps({"action": "add", "content": "新偏好"}, ensure_ascii=False)
        model = FakeRawModel([_raw(f"决策如下：\n```json\n{payload}\n```")])

        result = await consolidate_preference(model, feedback="f", selection_text="s", existing=[])

        assert result.action == "add"
        assert result.content == "新偏好"
        assert len(model.prompts) == 1  # 手工恢复成功，不触发重试

    async def test_manual_parse_fail_then_retry_succeeds(self) -> None:
        model = FakeRawModel(
            [
                _raw("# 偏好提炼\n仅说明文字，不含 JSON。"),
                _parsed(PreferenceDecision(action="noop")),
            ]
        )

        result = await consolidate_preference(model, feedback="f", selection_text="s", existing=[])

        assert result.action == "noop"
        assert len(model.prompts) == 2
        assert "无法解析" in model.prompts[1]
        assert "JSON" in model.prompts[1]

    async def test_all_attempts_fail_raises(self) -> None:
        model = FakeRawModel(
            [
                _raw("# 偏好提炼\n纯 Markdown 输出。"),
                _raw("# 偏好提炼\n仍是 Markdown 输出。"),
            ]
        )

        with pytest.raises(PreferenceExtractionError, match="偏好提炼失败"):
            await consolidate_preference(model, feedback="f", selection_text="s", existing=[])

        assert len(model.prompts) == 2


class TestVerbatimFallback:
    def test_format(self) -> None:
        text = verbatim_preference("  这道题太简单  ")
        assert text == "用户反馈（原文沉淀）：这道题太简单"

    def test_truncated(self) -> None:
        text = verbatim_preference("长" * (MAX_PREFERENCE_CHARS + 10))
        assert len(text) == MAX_PREFERENCE_CHARS
