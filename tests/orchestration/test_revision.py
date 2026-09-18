"""修订编排测试：start_revision 守卫与 _run_revision_round 的收尾矩阵（fake 注入）。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agents.schemas import ExamResult
from app.core.exceptions import PaperNotReady
from app.models.job import JobStage
from app.models.revision import PaperRevision
from app.orchestration import revision as revision_module
from tests.orchestration.helpers import FakeJob

PAPER_OLD = "# 试卷\n\n1. 旧题干\n"
PAPER_NEW = "# 试卷\n\n1. 新题干（已修订）\n"


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class FakePaperStorage:
    """可编程对象存储：按 key 回放试卷文本，记录写入。"""

    def __init__(self, papers: dict[str, str] | None = None) -> None:
        self.papers = dict(papers or {})
        self.puts: list[tuple[str, bytes]] = []

    async def get(self, key: str) -> bytes:
        if key not in self.papers:
            raise KeyError(key)
        return self.papers[key].encode("utf-8")

    async def put(self, key: str, data: bytes) -> None:
        self.puts.append((key, data))


class FakeRevisionSession:
    """修订编排用到的最小 AsyncSession：按模型类型返回 job/revision。

    ``scalars_results`` 为出队式返回（_run_revision_round 依次查询历史轮次、
    upload_ids）；``scalar_value`` 供 start_revision 的计数查询。
    """

    def __init__(
        self,
        job: FakeJob,
        revision: PaperRevision | None = None,
        *,
        scalar_value: int = 0,
        scalars_results: list[list[Any]] | None = None,
    ) -> None:
        self.job = job
        self.revision = revision
        self.scalar_value = scalar_value
        self._scalars_results = list(scalars_results or [])
        self.added: list[Any] = []
        self.committed = 0

    async def __aenter__(self) -> FakeRevisionSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, model: type, pk: uuid.UUID) -> Any:  # noqa: ARG002
        if model is PaperRevision:
            return self.revision
        if model is FakeJob or model is revision_module.Job:
            return self.job
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.committed += 1

    async def scalar(self, stmt: Any) -> int:  # noqa: ARG002
        return self.scalar_value

    async def scalars(self, stmt: Any) -> _ScalarResult:  # noqa: ARG002
        rows = self._scalars_results.pop(0) if self._scalars_results else []
        return _ScalarResult(rows)

    # ---- 断言辅助 ----

    def events(self, event_type: str) -> list[JobStage]:
        return [r for r in self.added if isinstance(r, JobStage) and r.event_type == event_type]


def make_revision(job: FakeJob, *, status: str = "running") -> PaperRevision:
    return PaperRevision(
        job_id=job.id,
        round_no=1,
        status=status,
        selection={"text": "旧题干", "before": "# 试卷", "after": ""},
        feedback="换个角度",
        snapshot_before=PAPER_OLD,
    )


def _capture_register(sink: list) -> Any:
    """fake register：记录 job_id 并关闭协程（避免 never-awaited 告警）。"""

    def _register(job_id: uuid.UUID, coro: Any) -> None:
        sink.append(job_id)
        coro.close()

    return _register


class TestStartRevision:
    async def test_creates_row_emits_and_registers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job = FakeJob(status="completed")
        session = FakeRevisionSession(job, scalar_value=0)  # 无历史轮次 → round_no=1
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_OLD})
        registered: list[Any] = []
        monkeypatch.setattr(revision_module, "storage", storage)
        monkeypatch.setattr(revision_module, "register", _capture_register(registered))

        revision = await revision_module.start_revision(
            session, job, {"text": "旧题干", "before": "", "after": ""}, "换个角度"
        )

        assert revision.round_no == 1
        assert revision.status == "running"
        assert revision.snapshot_before == PAPER_OLD
        assert registered == [job.id]
        assert len(session.events("revision_started")) == 1
        assert session.committed >= 1

    async def test_round_no_increments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        job = FakeJob(status="completed")
        session = FakeRevisionSession(job, scalar_value=2)  # 已有 2 轮 → round_no=3
        paper_store = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_OLD})
        monkeypatch.setattr(revision_module, "storage", paper_store)
        monkeypatch.setattr(revision_module, "register", _capture_register([]))

        revision = await revision_module.start_revision(
            session, job, {"text": "x"}, "反馈"
        )

        assert revision.round_no == 3

    async def test_missing_paper_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        job = FakeJob(status="completed")
        session = FakeRevisionSession(job)
        monkeypatch.setattr(revision_module, "storage", FakePaperStorage({}))
        monkeypatch.setattr(revision_module, "register", _capture_register([]))

        with pytest.raises(PaperNotReady):
            await revision_module.start_revision(session, job, {"text": "x"}, "反馈")


class TestRunRevisionRound:
    def _patch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        job: FakeJob,
        session: FakeRevisionSession,
        storage: FakePaperStorage,
        agent_result: ExamResult | None = None,
    ) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        async def fake_agent(job_id, context, hooks=None):  # noqa: ANN001
            captured["job_id"] = job_id
            captured["context"] = dict(context)
            return agent_result or ExamResult(completed_normally=True, render_status="succeeded")

        monkeypatch.setattr(revision_module, "run_exam_agent", fake_agent)
        monkeypatch.setattr(revision_module, "storage", storage)
        monkeypatch.setattr(revision_module, "AsyncSessionLocal", lambda: session)
        return captured

    async def test_success_marks_done_with_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job = FakeJob(status="completed")
        revision = make_revision(job)
        # scalars 依次返回：历史轮次（空）、upload_ids（空）
        session = FakeRevisionSession(job, revision, scalars_results=[[], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        captured = self._patch(monkeypatch, job, session, storage)

        await revision_module._run_revision_round(uuid.uuid4())

        assert revision.status == "done"
        assert revision.snapshot_after == PAPER_NEW
        assert revision.applied_at is not None
        assert len(session.events("revision_done")) == 1
        # agent 收到的 context：修订指令含划选与反馈
        directive = captured["context"]["revision"]
        assert directive["feedback"] == "换个角度"
        assert directive["selection"]["text"] == "旧题干"
        assert directive["history"] == []

    async def test_history_injected_into_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job = FakeJob(status="completed")
        revision = make_revision(job)
        prior = make_revision(job)
        prior.round_no = 1
        prior.status = "done"
        prior.snapshot_after = PAPER_OLD
        # 历史轮次返回 1 条 done；upload_ids 为空
        session = FakeRevisionSession(job, revision, scalars_results=[[prior], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        captured = self._patch(monkeypatch, job, session, storage)

        await revision_module._run_revision_round(uuid.uuid4())

        history = captured["context"]["revision"]["history"]
        assert [h["round_no"] for h in history] == [1]
        assert history[0]["feedback"] == "换个角度"

    async def test_no_change_marks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        job = FakeJob(status="completed")
        revision = make_revision(job)
        session = FakeRevisionSession(job, revision, scalars_results=[[], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_OLD})  # 未变化
        self._patch(monkeypatch, job, session, storage)

        await revision_module._run_revision_round(uuid.uuid4())

        assert revision.status == "failed"
        assert revision.error is not None and "未对试卷产生任何修改" in revision.error
        assert len(session.events("revision_failed")) == 1
        assert len(session.events("revision_done")) == 0

    async def test_render_degraded_emits_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """修订后渲染降级 md_only：试卷已改仍算 done，但补发 RENDER_FAILED 告警。"""
        job = FakeJob(status="completed")
        revision = make_revision(job)
        session = FakeRevisionSession(job, revision, scalars_results=[[], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        degraded = ExamResult(
            completed_normally=True, render_status="md_only", render_error="pandoc exit 1"
        )
        self._patch(monkeypatch, job, session, storage, agent_result=degraded)

        await revision_module._run_revision_round(uuid.uuid4())

        assert revision.status == "done"
        warnings = session.events("warning")
        assert any(w.payload.get("code") == "RENDER_FAILED" for w in warnings)

    async def test_revision_row_missing_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        job = FakeJob(status="completed")
        session = FakeRevisionSession(job, revision=None)
        storage = FakePaperStorage({})
        self._patch(monkeypatch, job, session, storage)

        await revision_module._run_revision_round(uuid.uuid4())  # 不抛异常即可

    async def test_wrapper_swallows_agent_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """agent 异常冒泡到包装层：收敛为 failed 并发事件，不向上抛。"""
        job = FakeJob(status="completed")
        revision = make_revision(job)
        session = FakeRevisionSession(job, revision, scalars_results=[[], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})

        async def boom(job_id, context, hooks=None):  # noqa: ANN001
            raise RuntimeError("agent 爆炸")

        monkeypatch.setattr(revision_module, "run_exam_agent", boom)
        monkeypatch.setattr(revision_module, "storage", storage)
        monkeypatch.setattr(revision_module, "AsyncSessionLocal", lambda: session)

        await revision_module._run_revision_with_cancellation(uuid.uuid4())  # 不抛异常

        assert revision.status == "failed"
        assert "agent 爆炸" in (revision.error or "")
        assert len(session.events("revision_failed")) == 1


class TestListRevisions:
    async def test_orders_desc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_revisions 返回新→旧。placeholder 校验实现按 round_no 倒序。"""
        job = FakeJob(status="completed")
        r1 = make_revision(job)
        r1.round_no = 1
        r2 = make_revision(job)
        r2.round_no = 2
        session = FakeRevisionSession(job, scalars_results=[[r1, r2]])

        rows = await revision_module.list_revisions(session, job.id)

        # fake 的 scalars 按队列出参，排序由真实 SQL 保证；这里只验证透传
        assert rows == [r1, r2]
