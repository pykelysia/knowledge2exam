"""管线意外失败的终态收敛测试：定格 failed 并推送 error/done 事件。"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.orchestration import stages as stages_module
from app.orchestration.stages import _run_pipeline_with_cancellation
from tests.orchestration.helpers import FakeJob, FakeSessionFactory


async def _noop_log(*args: object, **kwargs: object) -> None:
    pass


@pytest.fixture
def mute_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """屏蔽 log_error 的文件写入，保持测试密闭。"""
    monkeypatch.setattr(stages_module, "log_error", _noop_log)


async def test_unexpected_error_settles_job_to_failed(
    mute_logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = FakeJob(status="generating")
    factory = FakeSessionFactory(job)
    monkeypatch.setattr(stages_module, "AsyncSessionLocal", factory)

    async def boom(job_id: uuid.UUID) -> None:
        raise RuntimeError("LLM 配置缺失")

    monkeypatch.setattr(stages_module, "run_pipeline", boom)

    with pytest.raises(RuntimeError, match="LLM 配置缺失"):
        await _run_pipeline_with_cancellation(job.id)

    # 任务定格为 failed，且写入了 error_code / finished_at
    assert job.status == "failed"
    assert job.error_code == "PIPELINE_FAILED"
    assert job.finished_at is not None

    # SSE 尾序列：error 之后 done{status: failed}
    session = factory.sessions[0]
    tail_types = [s.event_type for s in session.stages]
    assert tail_types == ["error", "done"]
    error_event, done_event = session.stages
    assert error_event.payload["error_code"] == "PIPELINE_FAILED"
    assert error_event.payload["message"] == "LLM 配置缺失"
    assert done_event.payload["status"] == "failed"


async def test_terminal_job_not_overwritten_by_failure_handler(
    mute_logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已完成任务的后续异常不得覆盖终态（is_terminal 守卫）。"""
    job = FakeJob(status="completed")
    factory = FakeSessionFactory(job)
    monkeypatch.setattr(stages_module, "AsyncSessionLocal", factory)

    async def boom(job_id: uuid.UUID) -> None:
        raise RuntimeError("事后异常")

    monkeypatch.setattr(stages_module, "run_pipeline", boom)

    with pytest.raises(RuntimeError):
        await _run_pipeline_with_cancellation(job.id)

    assert job.status == "completed"
    assert job.finished_at is None
    assert factory.sessions[0].stages == []


async def test_cancellation_still_marks_cancelled(
    mute_logs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归：CancelledError 路径行为不变（定格 cancelled + done）。"""
    job = FakeJob(status="preprocessing")
    factory = FakeSessionFactory(job)
    monkeypatch.setattr(stages_module, "AsyncSessionLocal", factory)

    async def cancelled(job_id: uuid.UUID) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(stages_module, "run_pipeline", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await _run_pipeline_with_cancellation(job.id)

    assert job.status == "cancelled"
    assert job.finished_at is not None
    session = factory.sessions[0]
    assert [s.event_type for s in session.stages] == ["done"]
    assert session.stages[0].payload["status"] == "cancelled"
