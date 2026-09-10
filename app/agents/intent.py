"""用户意图提取：把用户文本材料结构化为 ExamIntent。

对应架构图中的「用户意图提取」节点，独立于 ReAct 循环，
产物注入系统预置提示词。
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

from app.agents.prompts import load_prompt, render_prompt
from app.agents.schemas import ExamIntent

logger = logging.getLogger(__name__)


async def extract_intent(
    model: BaseChatModel,
    *,
    duration_minutes: int,
    keypoint_list: str | None = None,
    extra_requirement: str | None = None,
) -> ExamIntent:
    """从重点清单与额外要求中提取结构化意图。

    提取失败时返回宽松的默认意图（不阻断主流程），材料原文仍会
    以工作区文件的形式供 agent 自行查阅。
    """
    template = load_prompt("intent")
    prompt = render_prompt(
        template,
        duration_minutes=str(duration_minutes),
        keypoints=keypoint_list or "（未提供）",
        extra_requirements=extra_requirement or "（未提供）",
    )
    try:
        structured = model.with_structured_output(ExamIntent)
        intent = await structured.ainvoke(prompt)
    except Exception as exc:
        logger.warning("意图提取失败，使用默认意图: %s", exc)
        return _fallback_intent()
    return intent if isinstance(intent, ExamIntent) else _fallback_intent()


def _fallback_intent() -> ExamIntent:
    return ExamIntent(
        exam_scope="（意图提取未成功，请以工作区材料为准自行判断考试范围）",
        summary="意图提取失败，依据材料自主组卷",
    )
