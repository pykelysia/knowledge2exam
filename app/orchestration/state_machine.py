"""任务状态机（docs/workflow.jpg：预处理 → agent 工作 → pdf 渲染 → 结果输出）。

本模块只做**状态合法性校验**与常量定义，不驱动真实作业流；
真实 pipeline（stages.py）负责推进状态。`generating` 覆盖 agent 工作
节点全程（内部蓝图规划、逐题产出与结果检验均在其中）。
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    pending = "pending"
    preprocessing = "preprocessing"
    generating = "generating"
    rendering = "rendering"
    completed = "completed"
    partially_completed = "partially_completed"
    failed = "failed"
    cancelled = "cancelled"


class Stage(StrEnum):
    preprocessing = "preprocessing"
    generating = "generating"
    rendering = "rendering"


TERMINAL_STATUSES = {
    JobStatus.completed,
    JobStatus.partially_completed,
    JobStatus.failed,
    JobStatus.cancelled,
}

# 状态机邻接表（仅记录正向推进；failed/cancelled 可从任意运行态进入）
_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.pending: {JobStatus.preprocessing, JobStatus.cancelled},
    JobStatus.preprocessing: {JobStatus.generating, JobStatus.failed, JobStatus.cancelled},
    JobStatus.generating: {JobStatus.rendering, JobStatus.failed, JobStatus.cancelled},
    JobStatus.rendering: {
        JobStatus.completed,
        JobStatus.partially_completed,
        JobStatus.failed,
        JobStatus.cancelled,
    },
}


def is_terminal(status: JobStatus | str) -> bool:
    return JobStatus(status) in TERMINAL_STATUSES


def can_transition(current: JobStatus | str, target: JobStatus | str) -> bool:
    cur = JobStatus(current)
    tgt = JobStatus(target)
    return tgt in _TRANSITIONS.get(cur, set())
