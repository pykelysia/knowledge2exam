"""进度事件总线（核心层）。

事件写入 `job_stage` 表（持久化），同时通过进程内 pub/sub 推送给 SSE 订阅者。
`seq` 单调递增，供 SSE 断线重连（`Last-Event-ID`）补发。
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import JobStage

# job_id -> 该任务的活跃订阅者队列集合
_subscribers: dict[uuid.UUID, set[asyncio.Queue]] = defaultdict(set)


def _jsonable(value: Any) -> Any:
    """把 payload 递归转换为 JSON 可序列化结构（UUID/Enum/Path 等）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


class EventBus:
    """向某个任务追加事件并广播。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def emit(
        self, job_id: uuid.UUID, event_type: str, data: dict[str, Any], stage: str | None = None
    ) -> int:
        """落盘一条事件，返回其 seq。"""
        data = _jsonable(data)
        max_seq = await self.db.scalar(
            select(func.coalesce(func.max(JobStage.seq), 0)).where(JobStage.job_id == job_id)
        )
        seq = (max_seq or 0) + 1

        self.db.add(
            JobStage(
                job_id=job_id,
                stage=stage or "",
                event_type=event_type,
                payload=data,
                seq=seq,
            )
        )
        await self.db.flush()

        for q in list(_subscribers.get(job_id, set())):
            q.put_nowait({"seq": seq, "event": event_type, "data": data})
        return seq

    async def emit_and_close(
        self, job_id: uuid.UUID, event_type: str, data: dict[str, Any], stage: str | None = None
    ) -> int:
        """落盘事件并向所有 SSE 订阅者发送关闭信号。"""
        seq = await self.emit(job_id, event_type, data, stage)
        for q in list(_subscribers.get(job_id, set())):
            q.put_nowait({"__close__": True, "seq": seq})
        return seq


def subscribe(job_id: uuid.UUID) -> asyncio.Queue:
    """注册一个 SSE 订阅者，返回其专属队列。"""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[job_id].add(q)
    return q


def unsubscribe(job_id: uuid.UUID, q: asyncio.Queue) -> None:
    _subscribers.get(job_id, set()).discard(q)


def has_subscribers(job_id: uuid.UUID) -> bool:
    return bool(_subscribers.get(job_id))
