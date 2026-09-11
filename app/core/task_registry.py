"""任务注册表：追踪运行中的 pipeline 任务并支持取消。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable

logger = logging.getLogger(__name__)

# job_id -> asyncio.Task 映射
_running_tasks: dict[uuid.UUID, asyncio.Task] = {}


def register(job_id: uuid.UUID, coro: Awaitable[None]) -> asyncio.Task:
    """将 coroutine 包装为 Task 并注册到注册表。"""
    task = asyncio.ensure_future(coro)
    _running_tasks[job_id] = task

    def _on_done(t: asyncio.Task) -> None:
        _running_tasks.pop(job_id, None)

    task.add_done_callback(_on_done)
    return task


def unregister(job_id: uuid.UUID) -> None:
    """手动取消注册（任务正常结束后也会自动取消注册）。"""
    _running_tasks.pop(job_id, None)


def get_task(job_id: uuid.UUID) -> asyncio.Task | None:
    """获取 job 对应的正在运行的任务。"""
    return _running_tasks.get(job_id)


def cancel(job_id: uuid.UUID) -> bool:
    """取消 job 对应的正在运行的任务。

    返回 True 表示成功发送取消信号，False 表示该 job 没有正在运行的任务。
    """
    task = _running_tasks.get(job_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def is_running(job_id: uuid.UUID) -> bool:
    """检查 job 是否还有正在运行的任务。"""
    task = _running_tasks.get(job_id)
    return task is not None and not task.done()
