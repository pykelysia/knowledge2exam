"""OpenAI 兼容 LLM 客户端。

统一封装与 LLM 的交互，支持不同 agent 角色使用不同模型档位。
配置来源见 config.py。
"""

from __future__ import annotations

import uuid
from typing import Any


class LLMClient:
    """LLM 调用客户端，封装 OpenAI 兼容协议。"""

    def __init__(self) -> None:
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI
            from app.config import settings
            self._client = AsyncOpenAI(
                api_key=getattr(settings, "llm_api_key", "sk-placeholder"),
                base_url=getattr(settings, "llm_base_url", "http://localhost:8000/v1"),
            )
        return self._client

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Any:
        """发起一次聊天补全调用。"""
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if max_tokens:
            kwargs["max_tokens"] = max_tokens

        return await client.chat.completions.create(**kwargs)


# 全局单例（延迟初始化，避免 import 时触发 OpenAI 凭证校验）
llm_client = LLMClient()
