"""修订编排测试：start_revision 守卫与 _run_revision_round 的收尾矩阵（fake 注入）。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agents.preference import PreferenceDecision
from app.agents.schemas import ExamResult
from app.config import settings
from app.core.exceptions import PaperNotReady
from app.models.job import JobStage
from app.models.revision import PaperRevision
from app.models.skill import CoursePreference
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


class TestRunRevisionRoundPreference:
    """轮首偏好沉淀：勾选时提炼落库，未勾选也注入既有偏好。"""

    def _patch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        job: FakeJob,
        session: FakeRevisionSession,
        storage: FakePaperStorage,
        *,
        decision: PreferenceDecision | None = None,
        consolidate_error: Exception | None = None,
    ) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        async def fake_agent(job_id, context, hooks=None):  # noqa: ANN001
            captured["context"] = dict(context)
            return ExamResult(completed_normally=True, render_status="succeeded")

        async def fake_consolidate(model, *, feedback, selection_text, existing):  # noqa: ANN001
            captured["consolidate"] = {
                "feedback": feedback,
                "selection_text": selection_text,
                "existing": list(existing),
            }
            if consolidate_error is not None:
                raise consolidate_error
            return decision

        monkeypatch.setattr(revision_module, "run_exam_agent", fake_agent)
        monkeypatch.setattr(revision_module, "consolidate_preference", fake_consolidate)
        monkeypatch.setattr(revision_module, "get_chat_model", lambda: object())
        monkeypatch.setattr(revision_module, "storage", storage)
        monkeypatch.setattr(revision_module, "AsyncSessionLocal", lambda: session)
        return captured

    @staticmethod
    def _scoped_job() -> FakeJob:
        job = FakeJob(status="completed")
        job.school_id = uuid.uuid4()
        job.course_id = uuid.uuid4()
        return job

    async def test_preference_saved_when_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """勾选沉淀：提炼 → 新增条目 → 事件与回显标记，偏好注入修订上下文。"""
        job = self._scoped_job()
        revision = make_revision(job)
        revision.save_preference = True
        existing = CoursePreference(
            school_id=job.school_id, course_id=job.course_id, content="既有偏好"
        )
        # scalars 依次：沉淀前既有条目、沉淀后重载、历史轮次、upload_ids
        session = FakeRevisionSession(
            job, revision, scalars_results=[[existing], [existing], [], []]
        )
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        decision = PreferenceDecision(action="add", content="题目应有综合性")
        captured = self._patch(monkeypatch, job, session, storage, decision=decision)

        await revision_module._run_revision_round(uuid.uuid4())

        added = [r for r in session.added if isinstance(r, CoursePreference)]
        assert len(added) == 1 and added[0].content == "题目应有综合性"
        assert revision.preference_saved is True
        assert len(session.events("preference_saved")) == 1
        assert captured["context"]["course_preferences"] == ["既有偏好"]
        # 提炼输入：本轮反馈与划选
        assert captured["consolidate"]["feedback"] == "换个角度"
        assert captured["consolidate"]["selection_text"] == "旧题干"

    async def test_llm_failure_falls_back_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """提炼失败：降级原文保存并告警，轮次继续。"""
        job = self._scoped_job()
        revision = make_revision(job)
        revision.save_preference = True
        session = FakeRevisionSession(job, revision, scalars_results=[[], [], [], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        self._patch(
            monkeypatch, job, session, storage, consolidate_error=RuntimeError("llm down")
        )

        await revision_module._run_revision_round(uuid.uuid4())

        added = [r for r in session.added if isinstance(r, CoursePreference)]
        assert len(added) == 1
        assert added[0].content.startswith("用户反馈（原文沉淀）：换个角度")
        assert revision.preference_saved is True
        warning_msgs = [w.payload.get("message", "") for w in session.events("warning")]
        assert any(
            msg.startswith("偏好提炼失败") and "RuntimeError" in msg for msg in warning_msgs
        )
        # 告警只暴露异常类名，不携带原始异常文本
        assert all("llm down" not in msg for msg in warning_msgs)

    async def test_empty_llm_content_uses_wired_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 返回 add 但内容为空：生产路径已接线 fallback，不静默丢沉淀请求。"""
        job = self._scoped_job()
        revision = make_revision(job)
        revision.save_preference = True
        session = FakeRevisionSession(job, revision, scalars_results=[[], [], [], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        decision = PreferenceDecision(action="add", content="  ")  # 模型抖动返回空内容
        self._patch(monkeypatch, job, session, storage, decision=decision)

        await revision_module._run_revision_round(uuid.uuid4())

        added = [r for r in session.added if isinstance(r, CoursePreference)]
        assert len(added) == 1
        assert added[0].content.startswith("用户反馈（原文沉淀）：换个角度")
        assert revision.preference_saved is True

    async def test_preferences_injected_even_when_not_checked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未勾选：不提炼不落库，但既有偏好仍注入上下文。"""
        job = self._scoped_job()
        revision = make_revision(job)
        existing = CoursePreference(
            school_id=job.school_id, course_id=job.course_id, content="既有偏好"
        )
        # scalars 依次：重载既有条目、历史轮次、upload_ids
        session = FakeRevisionSession(job, revision, scalars_results=[[existing], [], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        captured = self._patch(monkeypatch, job, session, storage)

        await revision_module._run_revision_round(uuid.uuid4())

        assert "consolidate" not in captured
        assert captured["context"]["course_preferences"] == ["既有偏好"]
        assert session.events("preference_saved") == []
        # 未勾选轮次不改写标记（未 flush 的 ORM 列默认值可为 None，取 falsy 语义）
        assert not revision.preference_saved

    async def test_cap_exceeded_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """条数触顶：不保存、置告警、preference_saved 保持 False。"""
        monkeypatch.setattr(settings, "max_course_preferences", 0)
        job = self._scoped_job()
        revision = make_revision(job)
        revision.save_preference = True
        session = FakeRevisionSession(job, revision, scalars_results=[[], [], [], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        decision = PreferenceDecision(action="add", content="新偏好")
        self._patch(monkeypatch, job, session, storage, decision=decision)

        await revision_module._run_revision_round(uuid.uuid4())

        assert [r for r in session.added if isinstance(r, CoursePreference)] == []
        assert revision.preference_saved is False
        assert any("上限" in w.payload.get("message", "") for w in session.events("warning"))

    async def test_preference_db_failure_fails_round(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """偏好落库 DB 层异常：冒泡到包装层收敛为 failed 并发事件，不向上抛。"""
        job = self._scoped_job()
        revision = make_revision(job)
        revision.save_preference = True
        session = FakeRevisionSession(job, revision, scalars_results=[[], []])
        storage = FakePaperStorage({f"jobs/{job.id}/output/paper.md": PAPER_NEW})
        self._patch(monkeypatch, job, session, storage)

        async def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("db down")

        monkeypatch.setattr(revision_module, "apply_preference_decision", boom)

        await revision_module._run_revision_with_cancellation(uuid.uuid4())

        assert revision.status == "failed"
        assert "db down" in (revision.error or "")
        assert len(session.events("revision_failed")) == 1
