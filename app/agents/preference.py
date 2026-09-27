"""课程偏好提炼：把修订反馈泛化为课程级持久偏好（mem0 式合并决策）。

输入 = 本轮反馈与划选 + 该课程已有偏好条目；输出结构化决策
ADD / UPDATE / DELETE / NOOP，由编排层执行落库。本模块不触数据库，
结构化输出经恢复链路（手工提取 JSON + 重试一次）仍失败时抛
PreferenceExtractionError，由调用方降级为原文保存（不阻断修订轮）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from app.agents.prompts import load_prompt, render_prompt
from app.agents.structured_output import structured_invoke

logger = logging.getLogger(__name__)

# 单条偏好规则的最大长度（字符），超出截断
MAX_PREFERENCE_CHARS = 300


class PreferenceExtractionError(RuntimeError):
    """偏好提炼失败：结构化输出解析与恢复重试均未成功。"""


class PreferenceDecision(BaseModel):
    """一次偏好提炼的合并决策。"""

    action: Literal["add", "update", "delete", "noop"] = Field(
        description="合并决策：add=新增偏好；update=改写/合并既有条目；"
        "delete=用户撤回某条偏好；noop=已被既有条目覆盖或无法泛化"
    )
    content: str = Field(
        default="", description="add/update 时泛化后的完整偏好规则（≤300 字）"
    )
    target_id: uuid.UUID | None = Field(
        default=None, description="update/delete 的目标条目 id"
    )


async def consolidate_preference(
    model: BaseChatModel,
    *,
    feedback: str,
    selection_text: str,
    existing: list[tuple[uuid.UUID, str]],
) -> PreferenceDecision:
    """把本轮反馈与既有偏好条目合并为一条结构化决策。

    existing 为该课程当前全部条目 (id, content)，旧→新。恢复与重试
    均失败时抛 PreferenceExtractionError，调用方负责失败兜底
    （原文保存）；本函数成功即返回合法决策。
    """
    template = load_prompt("preference")
    existing_text = (
        "\n".join(f"- id={entry_id}：{content}" for entry_id, content in existing)
        or "（暂无）"
    )
    prompt = render_prompt(
        template,
        feedback=feedback,
        selection_text=selection_text or "（未划选）",
        existing_entries=existing_text,
    )
    decision = await structured_invoke(
        model, PreferenceDecision, prompt, label="偏好提炼"
    )
    if decision is None:
        raise PreferenceExtractionError("偏好提炼失败：结构化输出解析与恢复重试均未成功")
    decision.content = decision.content.strip()[:MAX_PREFERENCE_CHARS]
    return decision


def verbatim_preference(feedback: str) -> str:
    """提炼失败时的原文兜底：保真降级，不丢用户勾选。"""
    return f"用户反馈（原文沉淀）：{feedback.strip()}"[:MAX_PREFERENCE_CHARS]
