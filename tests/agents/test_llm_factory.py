"""LLM 工厂单元测试：reasoning_effort 的透传与配置校验。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agents import llm as llm_module
from app.config import REASONING_EFFORT_VALUES, Settings


def test_get_chat_model_defaults_to_no_reasoning_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置时不发送 reasoning_effort（非推理模型/兼容后端不受影响）。"""
    monkeypatch.setattr(llm_module.settings, "agent_reasoning_effort", None)
    model = llm_module.get_chat_model()
    assert model.reasoning_effort is None


def test_get_chat_model_passes_reasoning_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置后透传到 ChatOpenAI。"""
    monkeypatch.setattr(llm_module.settings, "agent_reasoning_effort", "high")
    model = llm_module.get_chat_model()
    assert model.reasoning_effort == "high"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("high", "high"),
        (" MEDIUM ", "medium"),
        ("Low", "low"),
        ("", None),
        ("   ", None),
    ],
)
def test_settings_normalizes_reasoning_effort(raw: str, expected: str | None) -> None:
    assert Settings(agent_reasoning_effort=raw).agent_reasoning_effort == expected


def test_settings_rejects_invalid_reasoning_effort() -> None:
    with pytest.raises(ValidationError, match="reasoning_effort"):
        Settings(agent_reasoning_effort="bogus")


def test_reasoning_effort_values_match_openai_sdk() -> None:
    """与 OpenAI SDK 的 ReasoningEffort 枚举保持一致。"""
    assert REASONING_EFFORT_VALUES == ("none", "minimal", "low", "medium", "high", "xhigh", "max")
