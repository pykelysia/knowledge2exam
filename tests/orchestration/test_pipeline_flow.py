"""_run_real_pipeline 的阶段序列与 agent 契约测试（fake 依赖注入）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.schemas import ExamResult
from app.orchestration import stages as stages_module
from app.orchestration.events import EventBus
from app.orchestration.stages import _run_real_pipeline
from tests.orchestration.helpers import FakeJob, FakeSession

# app/agents 的 context 契约键（守护接口不漂移）
EXPECTED_CONTEXT_KEYS = {
    "duration_minutes",
    "need_explanation",
    "max_retries",
    "user_id",
    "school_id",
    "course_id",
    "upload_ids",
}


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch):
    """注入 fake 预处理 / fake agent / fake 入库，捕获 agent 收到的 context。"""
    captured: dict[str, Any] = {}

    async def fake_preprocess(db, job, bus):  # noqa: ANN001
        return {"materials": []}

    async def fake_run_exam_agent(job_id, context, hooks=None):  # noqa: ANN001
        captured["job_id"] = job_id
        captured["context"] = dict(context)
        return ExamResult(
            summary="fake done",
            completed_normally=True,
            render_status="succeeded",
        )

    async def fake_persist(db, job_id, result):  # noqa: ANN001
        return {"total_questions": 0, "total_plan_items": 0, "abandoned": 0}

    monkeypatch.setattr(stages_module, "_preprocess", fake_preprocess)
    monkeypatch.setattr(stages_module, "run_exam_agent", fake_run_exam_agent)
    monkeypatch.setattr(stages_module, "persist_exam_result", fake_persist)
    return captured


async def test_stage_sequence_matches_workflow(fakes) -> None:
    """stage_changed 序列恰为 preprocessing → generating → rendering。"""
    job = FakeJob(status="pending", duration_minutes=90, need_explanation=False)
    session = FakeSession(job)
    bus = EventBus(session)

    await _run_real_pipeline(session, job, bus)

    stage_events = session.events("stage_changed")
    assert [s.payload["stage"] for s in stage_events] == [
        "preprocessing",
        "generating",
        "rendering",
    ]
    # previous 链正确衔接
    assert stage_events[0].payload["previous"] == "pending"
    assert stage_events[1].payload["previous"] == "preprocessing"
    assert stage_events[2].payload["previous"] == "generating"
    # 渲染事件由编排层补发（fake agent 未触发 on_render_start）
    assert stage_events[2].stage == "rendering"

    # 终态：渲染成功 → completed，产物键就位
    assert job.status == "completed"
    assert job.md_key == f"jobs/{job.id}/output/paper.md"
    assert job.pdf_key == f"jobs/{job.id}/output/paper.pdf"

    # done 收尾事件
    done = session.events("done")
    assert len(done) == 1
    assert done[0].payload["status"] == "completed"


async def test_agent_context_contract(fakes) -> None:
    """传给 run_exam_agent 的 context 恰为六个契约键（app/agents 接口守护）。"""
    job = FakeJob(status="pending", duration_minutes=120, need_explanation=True)
    session = FakeSession(job)
    bus = EventBus(session)

    await _run_real_pipeline(session, job, bus)

    context = fakes["context"]
    assert set(context) == EXPECTED_CONTEXT_KEYS
    assert context["duration_minutes"] == 120
    assert context["need_explanation"] is True
    assert context["user_id"] == job.user_id
    assert fakes["job_id"] == job.id


async def test_no_legacy_stage_names_in_events(fakes) -> None:
    """任何事件的 stage 字段与载荷中都不再出现 planning / reviewing。"""
    job = FakeJob(status="pending")
    session = FakeSession(job)
    bus = EventBus(session)

    await _run_real_pipeline(session, job, bus)

    for event in session.stages:
        assert event.stage not in ("planning", "reviewing")
        assert "planning" not in str(event.payload)
        assert "reviewing" not in str(event.payload)


async def test_md_only_result_ends_partially_completed(fakes, monkeypatch) -> None:
    """agent 渲染降级（md_only）时终态为 partially_completed 且无 pdf_key。"""

    async def degraded_agent(job_id, context, hooks=None):  # noqa: ANN001
        return ExamResult(
            summary="md only",
            completed_normally=True,
            render_status="md_only",
            render_error="pandoc failed",
        )

    monkeypatch.setattr(stages_module, "run_exam_agent", degraded_agent)

    job = FakeJob(status="pending")
    session = FakeSession(job)
    bus = EventBus(session)

    await _run_real_pipeline(session, job, bus)

    assert job.status == "partially_completed"
    assert job.md_key == f"jobs/{job.id}/output/paper.md"
    assert job.pdf_key is None
    warnings = session.events("warning")
    assert any(w.payload.get("code") == "RENDER_FAILED" for w in warnings)
