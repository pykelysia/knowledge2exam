"""视觉 LLM OCR：使用 OpenAI 兼容多模态协议提取图片文本。"""

from __future__ import annotations

import base64

from app.config import settings


class VisionLLMOCR:
    """使用视觉 LLM 提取图片中的文本。"""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model

    @classmethod
    def from_settings(cls) -> VisionLLMOCR:
        """从配置创建实例。"""
        return cls(
            api_key=getattr(settings, "llm_api_key", ""),
            base_url=getattr(settings, "llm_base_url", "http://localhost:8000/v1"),
            model=getattr(settings, "ocr_model", "gpt-4o"),
        )

    async def extract_text(self, image_bytes: bytes) -> str:
        """调用视觉 LLM 提取图片中的文本。"""
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("openai 未安装，请运行 `pip install openai`") from exc

        client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

        # 将图片转为 base64
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # 构造多模态请求
        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "请提取这张图片中的所有文字内容。"
                                "只返回提取的文字，不要添加任何解释或说明。"
                                "如果图片中没有文字，返回空字符串。"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                                "detail": "high",
                            },
                        },
                    ],
                }
            ],
            max_tokens=4096,
            temperature=0.0,
        )

        text = response.choices[0].message.content or ""
        return text.strip()
