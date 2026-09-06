"""LangChain ChatOpenAI LLM 客户端。

统一封装与 LLM 的交互，支持不同 agent 角色使用不同模型档位。
配置来源见 config.py。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI


class LLMClient:
    """LLM 调用客户端，基于 LangChain ChatOpenAI。"""

    def __init__(self) -> None:
        self._client: ChatOpenAI | None = None

    def _get_client(self, model: str) -> ChatOpenAI:
        if self._client is None or getattr(self._client, "model", None) != model:
            from app.config import settings
            self._client = ChatOpenAI(
                model=model,
                api_key=getattr(settings, "llm_api_key", "sk-placeholder"),
                base_url=getattr(settings, "llm_base_url", "http://localhost:8000/v1"),
            )
        return self._client

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Any:
        """发起一次聊天补全调用，返回 LangChain AIMessage。"""
        client = self._get_client(model)
        if tools:
            client = client.bind_tools(tools)
        kwargs: dict[str, Any] = {
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        return await client.ainvoke(messages, **kwargs)


# 全局单例（延迟初始化，避免 import 时触发 OpenAI 凭证校验）
llm_client = LLMClient()
