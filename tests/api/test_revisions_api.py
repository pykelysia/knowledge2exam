"""修订 API 端点测试：假会话 + 依赖覆盖，不触真实数据库与任务。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import revisions as revisions_module
from app.core.db import get_db
from app.core.deps import get_current_user
from app.main import app
from tests.orchestration.helpers import FakeJob


class FakeApiResult:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeApiSession:
    """修订端点用到的最小 AsyncSession 假实现。"""

    def __init__(self, job: FakeJob) -> None:
        self.job = job
        self.committed = 0

    async def get(self, model: type, pk: uuid.UUID) -> FakeJob | None:  # noqa: ARG002
        return self.job

    async def commit(self) -> None:
        self.committed += 1


def revision_payload(feedback: str = "加大难度") -> dict:
    return {
        "selection": {"text": "1+1=？", "before": "选择题", "after": "A. 1"},
        "feedback": feedback,
    }


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    job = FakeJob(status="completed")
    session = FakeApiSession(job)
    user = SimpleNamespace(id=job.user_id)
    captured: dict[str, Any] = {}

    async def fake_start(db, job_, selection, feedback):  # noqa: ANN001
        captured["selection"] = selection
        captured["feedback"] = feedback
        return SimpleNamespace(id=uuid.uuid4(), round_no=1, status="running")

    async def fake_list(db, job_id):  # noqa: ANN001
        captured["list_job_id"] = job_id
        return []

    monkeypatch.setattr(revisions_module, "start_revision", fake_start)
    monkeypatch.setattr(revisions_module, "list_revisions", fake_list)
    monkeypatch.setattr(revisions_module, "is_running", lambda job_id: False)

    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user

    async def _client() -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _client, session, job, captured
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


class TestCreateRevision:
    async def test_accepted(self, api) -> None:
        client, _session, job, captured = api
        async with await client() as http:
            res = await http.post(f"/api/v1/jobs/{job.id}/revisions", json=revision_payload())

        assert res.status_code == 202
        body = res.json()
        assert body["round_no"] == 1
        assert body["status"] == "running"
        assert uuid.UUID(body["revision_id"])
        # 划选锚点原样透传给编排层
        assert captured["selection"]["text"] == "1+1=？"
        assert captured["feedback"] == "加大难度"

    async def test_job_not_finished_conflicts(self, api) -> None:
        client, _session, job, _captured = api
        job.status = "generating"
        async with await client() as http:
            res = await http.post(f"/api/v1/jobs/{job.id}/revisions", json=revision_payload())

        assert res.status_code == 409
        assert res.json()["error_code"] == "JOB_NOT_FINISHED"

    async def test_revision_in_progress_conflicts(self, api, monkeypatch) -> None:
        client, _session, job, _captured = api
        monkeypatch.setattr(revisions_module, "is_running", lambda job_id: True)
        async with await client() as http:
            res = await http.post(f"/api/v1/jobs/{job.id}/revisions", json=revision_payload())

        assert res.status_code == 409
        assert res.json()["error_code"] == "REVISION_IN_PROGRESS"

    async def test_empty_feedback_rejected(self, api) -> None:
        client, _session, job, _captured = api
        async with await client() as http:
            res = await http.post(
                f"/api/v1/jobs/{job.id}/revisions", json=revision_payload(feedback="")
            )

        assert res.status_code == 400

    async def test_unknown_job_not_found(self, api) -> None:
        client, session, _job, _captured = api
        session.job = None
        async with await client() as http:
            res = await http.post(
                f"/api/v1/jobs/{uuid.uuid4()}/revisions", json=revision_payload()
            )

        assert res.status_code == 404
        assert res.json()["error_code"] == "JOB_NOT_FOUND"


class TestListRevisions:
    async def test_returns_history(self, api) -> None:
        client, _session, job, captured = api
        async with await client() as http:
            res = await http.get(f"/api/v1/jobs/{job.id}/revisions")

        assert res.status_code == 200
        body = res.json()
        assert body["job_id"] == str(job.id)
        assert body["total"] == 0
        assert body["revisions"] == []
        assert captured["list_job_id"] == job.id


class TestQuestionsEndpointRemoved:
    async def test_questions_endpoint_gone(self, api) -> None:
        """分题端点已随整卷直出退场：返回 404。"""
        client, _session, job, _captured = api
        async with await client() as http:
            res = await http.get(f"/api/v1/jobs/{job.id}/questions")

        assert res.status_code == 404
