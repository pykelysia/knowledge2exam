"""状态机与 workflow.jpg 阶段序列的一致性测试。"""

from __future__ import annotations

from app.orchestration.state_machine import (
    _TRANSITIONS,
    TERMINAL_STATUSES,
    JobStatus,
    Stage,
    can_transition,
    is_terminal,
)


def test_status_values_match_workflow():
    """状态集合与图中节点一一对应：预处理 → agent 工作(generating) → 渲染。"""
    assert set(JobStatus.__members__) == {
        "pending",
        "preprocessing",
        "generating",
        "rendering",
        "completed",
        "partially_completed",
        "failed",
        "cancelled",
    }
    assert set(Stage.__members__) == {"preprocessing", "generating", "rendering"}
    # 审查不再是独立阶段
    assert "planning" not in JobStatus.__members__
    assert "reviewing" not in JobStatus.__members__


def test_happy_path_transitions():
    path = [
        (JobStatus.pending, JobStatus.preprocessing),
        (JobStatus.preprocessing, JobStatus.generating),
        (JobStatus.generating, JobStatus.rendering),
        (JobStatus.rendering, JobStatus.completed),
        (JobStatus.rendering, JobStatus.partially_completed),
    ]
    for cur, tgt in path:
        assert can_transition(cur, tgt), f"{cur} -> {tgt} 应合法"


def test_terminal_statuses():
    assert TERMINAL_STATUSES == {
        JobStatus.completed,
        JobStatus.partially_completed,
        JobStatus.failed,
        JobStatus.cancelled,
    }
    assert is_terminal("completed")
    assert is_terminal(JobStatus.failed)
    assert not is_terminal("generating")


def test_failure_reachable_from_running_states():
    for running in (JobStatus.preprocessing, JobStatus.generating, JobStatus.rendering):
        assert can_transition(running, JobStatus.failed)
        assert can_transition(running, JobStatus.cancelled)


def test_no_outgoing_edges_from_terminal_states():
    """终态不再有正向出边（含旧版 partially_completed -> rendering 残留）。"""
    for terminal in TERMINAL_STATUSES:
        assert not _TRANSITIONS.get(terminal), f"{terminal} 不应有出边"
