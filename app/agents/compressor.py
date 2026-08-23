"""压缩 agent（compressor）。"""

from __future__ import annotations

from app.agents.llm import llm_client
from app.agents.prompts import load_prompt


async def compress_text(
    raw_text: str,
    target_tokens: int = 2000,
    model: str = "gpt-4o-mini",
) -> str:
    """压缩超长文本，保留关键信息点。"""

    template = load_prompt("compressor")
    prompt = template.format(target_tokens=target_tokens, raw_text=raw_text)

    response = await llm_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": "你是文本压缩助手，擅长在保留关键信息的前提下精简文本。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=target_tokens,
    )

    return response.choices[0].message.content or raw_text
