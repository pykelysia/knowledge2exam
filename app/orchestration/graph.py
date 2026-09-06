"""LangGraph 编排图（已迁移至主从式架构）。

该模块已重构为使用 MainAgent（LangGraph StateGraph + ToolNode）。
旧的 planner -> fanout -> reviewer -> rendering 流水线已迁移至：
- app/agents/main_agent.py：MainAgent 实现
- app/orchestration/stages.py：编排入口
- app/orchestration/integration.py：文件整合到数据库

此文件保留为向后兼容的包装器，实际逻辑委托给 stages.run_pipeline。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.orchestration.events import EventBus
from app.orchestration.state_machine import JobStatus, Stage
from app.orchestration.stages import run_pipeline


async def run_graph(
    db_factory: Any,
    bus_factory: Any,
    job_id: uuid.UUID,
    context: dict[str, Any],
) -> None:
    """执行编排图（向后兼容包装器）。"""

    # 直接调用新的 pipeline，它内部使用 MainAgent
    await run_pipeline(job_id)
