"""orchestration/preference 单元测试：四类决策执行、作用域防护与条数上限。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agents.preference import PreferenceDecision
from app.config import settings
from app.models.revision import PaperRevision
from app.models.skill import CoursePreference
from app.orchestration.preference import apply_preference_decision
from tests.orchestration.helpers import FakeJob

SCHOOL = uuid.uuid4()
COURSE = uuid.uuid4()


class FakePrefSession:
    """apply_preference_decision 用到的最小 AsyncSession。"""

    def __init__(self, rows: list[CoursePreference] | None = None, count: int = 0) -> None:
        self.rows = list(rows or [])
        self.count = count
        self.added: list[Any] = []
        self.deleted: list[Any] = []

    async def get(self, model: type, pk: uuid.UUID) -> Any:  # noqa: ARG002
        return next((r for r in self.rows if r.id == pk), None)

    async def scalar(self, stmt: Any) -> int:  # noqa: ARG002
        return self.count

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)


def make_row(content: str = "既有偏好", *, school: uuid.UUID = SCHOOL) -> CoursePreference:
    return CoursePreference(
        school_id=school, course_id=COURSE, content=content, id=uuid.uuid4()
    )


def make_ctx() -> tuple[FakeJob, PaperRevision]:
    job = FakeJob(status="completed")
    job.school_id = SCHOOL
    job.course_id = COURSE
    revision = PaperRevision(job_id=job.id, round_no=1, feedback="换个角度")
    return job, revision


class TestApplyAdd:
    async def test_add_creates_scoped_row(self) -> None:
        job, revision = make_ctx()
        session = FakePrefSession(count=0)
        decision = PreferenceDecision(action="add", content="题目应有综合性")

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is True
        (row,) = session.added
        assert isinstance(row, CoursePreference)
        assert row.school_id == SCHOOL and row.course_id == COURSE
        assert row.content == "题目应有综合性"
        assert row.source_revision_id == revision.id
        assert row.created_by == job.user_id

    async def test_add_at_cap_skips_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "max_course_preferences", 1)
        job, revision = make_ctx()
        session = FakePrefSession(count=1)  # 已达上限
        decision = PreferenceDecision(action="add", content="新偏好")

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is False and result.warn is True
        assert session.added == []
        assert "上限" in result.note

    async def test_empty_content_uses_fallback(self) -> None:
        job, revision = make_ctx()
        session = FakePrefSession()
        decision = PreferenceDecision(action="add", content="  ")

        result = await apply_preference_decision(
            session,
            job=job,
            revision=revision,
            decision=decision,
            fallback_content="用户反馈（原文沉淀）：换个角度",
        )

        assert result.saved is True
        assert session.added[0].content.startswith("用户反馈（原文沉淀）")

    async def test_empty_content_and_fallback_skips_save(self) -> None:
        """LLM 空内容且无回退：不落库不告警，note 说明原因。"""
        job, revision = make_ctx()
        session = FakePrefSession(count=0)
        decision = PreferenceDecision(action="add", content="   ")

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision, fallback_content="  "
        )

        assert result.saved is False and result.warn is False
        assert session.added == []
        assert "为空" in result.note


class TestApplyUpdate:
    async def test_update_rewrites_target(self) -> None:
        job, revision = make_ctx()
        row = make_row("旧偏好")
        session = FakePrefSession(rows=[row])
        decision = PreferenceDecision(action="update", content="合并后的新偏好", target_id=row.id)

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is True
        assert row.content == "合并后的新偏好"
        assert session.added == []

    async def test_update_unknown_target_falls_back_to_add(self) -> None:
        job, revision = make_ctx()
        session = FakePrefSession(rows=[])
        decision = PreferenceDecision(action="update", content="偏好", target_id=uuid.uuid4())

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is True
        assert len(session.added) == 1

    async def test_update_other_scope_target_falls_back_to_add(self) -> None:
        """跨作用域 target 不生效，降级为本作用域新增（防误改他课偏好）。"""
        job, revision = make_ctx()
        row = make_row(school=uuid.uuid4())  # 他校条目
        session = FakePrefSession(rows=[row])
        decision = PreferenceDecision(action="update", content="偏好", target_id=row.id)

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is True
        assert row.content == "既有偏好"  # 未被改写
        assert len(session.added) == 1


class TestApplyDeleteAndNoop:
    async def test_delete_removes_target(self) -> None:
        job, revision = make_ctx()
        row = make_row()
        session = FakePrefSession(rows=[row])
        decision = PreferenceDecision(action="delete", target_id=row.id)

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is True
        assert session.deleted == [row]

    async def test_delete_missing_target_is_noop(self) -> None:
        job, revision = make_ctx()
        session = FakePrefSession(rows=[])
        decision = PreferenceDecision(action="delete", target_id=uuid.uuid4())

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is False and result.warn is False

    async def test_delete_other_scope_target_is_rejected(self) -> None:
        """跨作用域 delete 目标：不删除、不降级新增，返回未变更。"""
        job, revision = make_ctx()
        row = make_row(school=uuid.uuid4())  # 他校条目
        session = FakePrefSession(rows=[row])
        decision = PreferenceDecision(action="delete", target_id=row.id)

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is False
        assert session.deleted == []
        assert "不存在" in result.note

    async def test_noop_changes_nothing(self) -> None:
        job, revision = make_ctx()
        session = FakePrefSession()
        decision = PreferenceDecision(action="noop")

        result = await apply_preference_decision(
            session, job=job, revision=revision, decision=decision
        )

        assert result.saved is False
        assert session.added == [] and session.deleted == []
