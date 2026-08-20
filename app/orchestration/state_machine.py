"""任务状态机（architecture.md 第 4 节）。

首版只做**状态合法性校验**与常量定义，不驱动真实作业流；
模拟 pipeline（stages.py）负责推进状态，并调用本模块校验每次转换。
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    pending = "pending"
    preprocessing = "preprocessing"
    planning = "planning"
    generating = "generating"
    reviewing = "reviewing"
    rendering = "rendering"
    completed = "completed"
    partially_completed = "partially_completed"
    failed = "failed"
    cancelled = "cancelled"


class Stage(StrEnum):
    preprocessing = "preprocessing"
    planning = "planning"
    generating = "generating"
    reviewing = "reviewing"
    rendering = "rendering"


TERMINAL_STATUSES = {
    JobStatus.completed,
    JobStatus.partially_completed,
    JobStatus.failed,
    JobStatus.cancelled,
}

# 状态机邻接表（仅记录正向推进；cancelled 可从任意运行态进入）
_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.pending: {JobStatus.preprocessing, JobStatus.cancelled},
    JobStatus.preprocessing: {JobStatus.planning, JobStatus.failed, JobStatus.cancelled},
    JobStatus.planning: {JobStatus.generating, JobStatus.failed, JobStatus.cancelled},
    JobStatus.generating: {
        JobStatus.reviewing,
        JobStatus.rendering,
        JobStatus.partially_completed,
        JobStatus.cancelled,
    },
    JobStatus.reviewing: {JobStatus.generating, JobStatus.rendering, JobStatus.cancelled},
    JobStatus.rendering: {
        JobStatus.completed,
        JobStatus.partially_completed,
        JobStatus.cancelled,
    },
    JobStatus.partially_completed: {JobStatus.rendering},
}


def is_terminal(status: JobStatus | str) -> bool:
    return JobStatus(status) in TERMINAL_STATUSES


def can_transition(current: JobStatus | str, target: JobStatus | str) -> bool:
    cur = JobStatus(current)
    tgt = JobStatus(target)
    return tgt in _TRANSITIONS.get(cur, set())
