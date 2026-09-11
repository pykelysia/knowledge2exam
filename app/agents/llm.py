"""ReAct agent 的 LLM 工厂。"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import settings


def get_chat_model() -> ChatOpenAI:
    """从全局配置构造 OpenAI 兼容的聊天模型。"""
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=SecretStr(settings.llm_api_key),
        base_url=settings.llm_base_url,
        temperature=settings.agent_temperature,
        timeout=settings.llm_timeout_seconds,
    )
