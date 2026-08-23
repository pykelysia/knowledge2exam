"""编排层 facade：接入层通过此模块触发任务，不直接依赖编排层内部实现。"""

from __future__ import annotations

import uuid

from app.orchestration.stages import run_pipeline


async def start_job(job_id: uuid.UUID) -> None:
    """启动任务的生成 pipeline。"""
    await run_pipeline(job_id)
