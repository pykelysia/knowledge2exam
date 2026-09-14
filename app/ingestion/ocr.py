"""视觉 LLM OCR：使用 OpenAI 兼容多模态协议提取图片文本。"""

from __future__ import annotations

import base64

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings

# 内嵌图片/插图：只要可读文本
_EXTRACT_TEXT_PROMPT = (
    "请提取这张图片中的所有文字内容。"
    "只返回提取的文字，不要添加任何解释或说明。"
    "如果图片中没有文字，返回空字符串。"
)

# 整页转录（PDF 损坏页兜底）：完整还原为 Markdown + LaTeX
_EXTRACT_MARKDOWN_PROMPT = (
    "你是专业的文档转录助手。请把这张图片完整转录为 Markdown 文本：\n"
    "1. 按阅读顺序输出所有内容，不要遗漏题号、选项与说明文字；\n"
    "2. 所有数学公式用 LaTeX 表示：行内公式用 $...$，独立公式用 $$...$$，"
    "分数、上下标、积分上下限按原样还原；\n"
    "3. 表格用 Markdown 表格；\n"
    "4. 无法辨认的字符用〔?〕占位，不要编造内容；\n"
    "5. 只输出转录结果本身，不要任何解释或前言。"
)


class VisionLLMOCR:
    """使用视觉 LLM 提取图片中的文本。"""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model

    @classmethod
    def from_settings(cls) -> VisionLLMOCR:
        """从配置创建实例：OCR 专用凭证优先，留空回落 LLM 配置。"""
        return cls(
            api_key=getattr(settings, "ocr_api_key", "")
            or getattr(settings, "llm_api_key", ""),
            base_url=getattr(settings, "ocr_base_url", "")
            or getattr(settings, "llm_base_url", "http://localhost:8000/v1"),
            model=getattr(settings, "ocr_model", "gpt-4o"),
        )

    async def extract_text(self, image_bytes: bytes) -> str:
        """调用视觉 LLM 提取图片中的纯文本（用于文档内嵌图片）。"""
        return await self._invoke(image_bytes, _EXTRACT_TEXT_PROMPT, max_tokens=4096)

    async def extract_markdown(self, image_bytes: bytes) -> str:
        """调用视觉 LLM 把整页内容转录为 Markdown（公式为 LaTeX）。"""
        return await self._invoke(image_bytes, _EXTRACT_MARKDOWN_PROMPT, max_tokens=8192)

    async def _invoke(self, image_bytes: bytes, prompt: str, *, max_tokens: int) -> str:
        client = ChatOpenAI(
            model=self._model,
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=getattr(settings, "llm_timeout_seconds", 120),
        )

        # 将图片转为 base64（按魔数声明 MIME，PNG 渲染图与 JPEG 压缩图均可能出现）
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        mime = "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{image_b64}",
                        "detail": "high",
                    },
                },
            ]
        )

        response = await client.ainvoke(
            [message],
            max_tokens=max_tokens,
            temperature=0.0,
        )

        return response.content.strip() if response.content else ""
