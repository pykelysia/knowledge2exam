"""任务 API 端点测试：假会话 + 依赖覆盖，不触真实数据库。

覆盖：任务快照（含 plan/progress 聚合路径）、列表端点、取消（成功与终态冲突）、
删除（取消任务 + 存储前缀清理）。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import jobs as jobs_module
from app.core.db import get_db
from app.core.deps import get_current_user
from app.main import app
from tests.orchestration.helpers import FakeJob


class FakeApiResult:
    """db.execute(...) 的返回：可 .all() 取行，rowcount 可编程。"""

    def __init__(self, rows: list[Any] | None = None, rowcount: int = 1) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeApiSession:
    """API 端点用到的最小 AsyncSession 假实现。"""

    def __init__(self, job: FakeJob) -> None:
        self.job = job
        self.committed = 0
        self.executed: list[Any] = []
        self.deleted: list[Any] = []
        self.scalar_values: list[Any] = []
        self.next_rowcount = 1

    async def get(self, model: type, pk: uuid.UUID) -> FakeJob | None:  # noqa: ARG002
        return self.job

    def add(self, obj: Any) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        pass

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def execute(self, stmt: Any) -> FakeApiResult:  # noqa: ARG002
        self.executed.append(stmt)
        return FakeApiResult(rowcount=self.next_rowcount)

    async def scalar(self, stmt: Any) -> Any:  # noqa: ARG002
        if self.scalar_values:
            return self.scalar_values.pop(0)
        return 0

    async def scalars(self, stmt: Any) -> FakeApiResult:  # noqa: ARG002
        return FakeApiResult(rows=[self.job])


class FakeStorage:
    """delete_prefix 记录调用。"""

    def __init__(self) -> None:
        self.deleted_prefixes: list[str] = []

    async def delete_prefix(self, prefix: str) -> int:
        self.deleted_prefixes.append(prefix)
        return 3


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, fake_storage: FakeStorage):
    """构造 (AsyncClient 工厂, FakeApiSession, FakeJob)，覆盖鉴权与 DB 依赖。"""
    job = FakeJob(status="completed", planned_total=3, md_key="jobs/x/output/paper.md")
    session = FakeApiSession(job)
    user = SimpleNamespace(id=job.user_id)  # 归属校验：与 job.user_id 一致

    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(jobs_module, "storage", fake_storage)

    async def _client() -> AsyncClient:
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield _client, session, job
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def fake_storage() -> FakeStorage:
    return FakeStorage()


class TestGetJob:
    async def test_snapshot_shape(self, api) -> None:
        client, _session, job = api
        async with await client() as http:
            res = await http.get(f"/api/v1/jobs/{job.id}")

        assert res.status_code == 200
        body = res.json()
        assert body["job_id"] == str(job.id)
        assert body["status"] == "completed"
        # 入库后 plan.total 来自蓝图聚合；无蓝图行时回落 planned_total
        assert body["plan"]["total"] == 3
        assert body["artifacts"]["md_url"] == f"/api/v1/jobs/{job.id}/paper.md"


class TestListJobs:
    async def test_list_returns_user_jobs(self, api) -> None:
        client, _session, job = api
        async with await client() as http:
            res = await http.get("/api/v1/jobs")

        assert res.status_code == 200
        body = res.json()
        assert [j["job_id"] for j in body["jobs"]] == [str(job.id)]


class TestCancelJob:
    async def test_cancel_running_job(self, api) -> None:
        client, session, job = api
        job.status = "generating"

        async with await client() as http:
            res = await http.post(f"/api/v1/jobs/{job.id}/cancel")

        assert res.status_code == 204
        assert session.committed >= 1
        # 条件 UPDATE 已执行且提交了 done 事件
        assert len(session.executed) == 1

    async def test_cancel_terminal_job_conflicts(self, api) -> None:
        client, session, job = api
        job.status = "completed"
        session.next_rowcount = 0  # 终态行不被条件 UPDATE 命中

        async with await client() as http:
            res = await http.post(f"/api/v1/jobs/{job.id}/cancel")

        assert res.status_code == 409
        assert res.json()["error_code"] == "JOB_ALREADY_FINISHED"


class TestDeleteJob:
    async def test_delete_cleans_storage_prefix(self, api, fake_storage) -> None:
        client, session, job = api

        async with await client() as http:
            res = await http.delete(f"/api/v1/jobs/{job.id}")

        assert res.status_code == 204
        assert fake_storage.deleted_prefixes == [f"jobs/{job.id}/"]
        assert session.deleted  # ORM 行已删除
