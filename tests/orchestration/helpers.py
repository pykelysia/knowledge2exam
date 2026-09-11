"""tests/orchestration 共享的假会话与假任务对象。

用于在不触碰真实数据库的前提下驱动 stages 的状态推进与事件总线：
FakeSession 实现 EventBus/_run_real_pipeline 用到的最小 AsyncSession 接口
（get/scalars/scalar/add/flush/commit）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models.job import JobStage


class _ScalarResult:
    """db.scalars(...) 的返回值：只关心 .all()。"""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


@dataclass
class FakeJob:
    """_run_real_pipeline 触碰到的 Job 属性子集。"""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID = field(default_factory=uuid.uuid4)
    school_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    status: str = "pending"
    duration_minutes: int = 100
    need_explanation: bool = False
    planned_total: int | None = None
    md_key: str | None = None
    pdf_key: str | None = None
    error_code: str | None = None
    finished_at: datetime | None = None


class FakeSession:
    """最小 AsyncSession 假实现；scalar 返回已记录事件的最大 seq。"""

    def __init__(self, job: FakeJob) -> None:
        self.job = job
        self.added: list[Any] = []
        self.committed = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, model: type, pk: uuid.UUID) -> FakeJob | None:  # noqa: ARG002
        return self.job

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.committed += 1

    async def refresh(self, obj: Any) -> None:
        pass

    async def scalar(self, stmt: Any) -> int:  # noqa: ARG002
        seqs = [row.seq for row in self.added if isinstance(row, JobStage)]
        return max(seqs, default=0)

    async def scalars(self, stmt: Any) -> _ScalarResult:  # noqa: ARG002
        return _ScalarResult([])

    # ---- 断言辅助 ----

    @property
    def stages(self) -> list[JobStage]:
        """按 seq 排序的已记录事件。"""
        return sorted((r for r in self.added if isinstance(r, JobStage)), key=lambda r: r.seq)

    def events(self, event_type: str) -> list[JobStage]:
        return [s for s in self.stages if s.event_type == event_type]


class FakeSessionFactory:
    """替换 stages.AsyncSessionLocal：每次调用返回新 FakeSession。"""

    def __init__(self, job: FakeJob) -> None:
        self.job = job
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession(self.job)
        self.sessions.append(session)
        return session
