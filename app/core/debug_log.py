"""日志工具。

- `log_step`：记录常规 debug 步骤，仅 `DEBUG_MODE=true` 时写入。
- `log_error`：记录任务进程中的错误，**始终写入** `logs/` 文件，不受 debug_mode 控制。
  使用独立 logger name `k2e.debug`，可在应用启动时单独配置 handler。
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from app.config import settings

logger = logging.getLogger("k2e.debug")


async def log_step(
    job_id: str,
    name: str,
    step: str = "function",
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    elapsed_ms: float = 0,
    stage: str | None = None,
) -> None:
    """记录一个 debug 步骤（仅 DEBUG_MODE=true 时写入）。"""
    if not settings.debug_mode:
        return

    # 精简输入输出，避免日志爆炸
    input_summary = _summarize(input)
    output_summary = _summarize(output)

    logger.info(
        "job=%s stage=%s step=%s name=%s elapsed_ms=%.1f input=%s output=%s",
        job_id,
        stage,
        step,
        name,
        elapsed_ms,
        input_summary,
        output_summary,
    )


async def log_error(
    job_id: str,
    exc: BaseException,
    stage: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """记录任务进程中出现的异常，始终写入日志文件。

    Args:
        job_id: 当前任务 ID。
        exc: 捕获到的异常对象。
        stage: 出错阶段名称（如 ``preprocessing`` / ``generating``）。
        context: 额外上下文信息（模型名、上传件数等），会被精简后写入。
    """
    # 精简 context，避免日志爆炸
    ctx_summary = _summarize(context) if context else {}

    logger.error(
        "job=%s stage=%s error_type=%s error=%s traceback=%s context=%s",
        job_id,
        stage,
        type(exc).__name__,
        str(exc),
        traceback.format_exc(limit=5),
        ctx_summary,
    )


def _summarize(data: dict[str, Any] | None) -> dict[str, Any]:
    """精简字典用于日志输出。"""
    if data is None:
        return {}
    summary: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, (str, int, float, bool)):
            summary[k] = v
        elif isinstance(v, list):
            summary[k] = f"<list len={len(v)}>"
        elif isinstance(v, dict):
            summary[k] = f"<dict keys={list(v.keys())}>"
        else:
            summary[k] = type(v).__name__
    return summary
