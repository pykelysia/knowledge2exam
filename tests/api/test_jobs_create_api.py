"""create_job 端点测试：引用授权 / 解析门禁 / 标签推导。

假会话 + 依赖覆盖，不触真实数据库；register 打桩避免真的启动 pipeline。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import jobs as jobs_module
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.enums import SourceType
from app.main import app
from app.models.job import JobUpload
from app.models.upload import Upload


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeCreateSession:
    """按调用顺序返回脚本化 scalars 结果的最小 AsyncSession 假实现。"""

    def __init__(self, upload_rows: list[Upload], imported_ids: list[uuid.UUID]) -> None:
        self._upload_rows = upload_rows
        self._imported_ids = imported_ids
        self.added: list[Any] = []
        self.committed = 0

    async def get(self, model: type, pk: uuid.UUID) -> Any:  # noqa: ARG002
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        # 真实 flush 会应用列默认值（uuid 主键 / created_at），假实现手动补齐
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(UTC)

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        pass

    async def scalars(self, stmt: Any) -> FakeResult:  # noqa: ARG002
        # 第 1 次：Upload.in_ 查询；第 2 次：FileImport 查询
        if not hasattr(self, "_upload_served"):
            self._upload_served = True
            return FakeResult(list(self._upload_rows))
        return FakeResult(list(self._imported_ids))


@pytest.fixture
def register_spy(monkeypatch: pytest.MonkeyPatch):
    """打桩 register：记录 job_id 并关闭协程，避免真的启动 pipeline。"""
    calls: list[uuid.UUID] = []

    def _fake_register(job_id: uuid.UUID, coro: Any) -> None:
        calls.append(job_id)
        coro.close()

    monkeypatch.setattr(jobs_module, "register", _fake_register)
    return calls


def _make_client(session: FakeCreateSession, user_id: uuid.UUID) -> AsyncClient:
    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: type(
        "U", (), {"id": user_id}
    )()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _cleanup() -> None:
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def _upload(
    user_id: uuid.UUID,
    *,
    source_type: SourceType = SourceType.book,
    parse_status: str = "succeeded",
    school_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
    deleted_at: datetime | None = None,
) -> Upload:
    return Upload(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type=source_type,
        filename="note.pdf",
        parse_status=parse_status,
        school_id=school_id,
        course_id=course_id,
        deleted_at=deleted_at,
    )


class TestCreateJob:
    async def test_own_parsed_upload_accepted(self, register_spy) -> None:
        user = uuid.uuid4()
        school, course = uuid.uuid4(), uuid.uuid4()
        upload = _upload(user, school_id=school, course_id=course)
        session = FakeCreateSession([upload], [])
        job_rows: list[Any] = []

        orig_add = session.add

        def _track(obj: Any) -> None:
            job_rows.append(obj)
            orig_add(obj)

        session.add = _track  # type: ignore[method-assign]

        try:
            async with _make_client(session, user) as http:
                res = await http.post(
                    "/api/v1/jobs",
                    json={"upload_ids": [str(upload.id)], "duration_minutes": 60},
                )
        finally:
            _cleanup()

        assert res.status_code == 202
        assert register_spy, "应注册后台 pipeline"
        job = next(o for o in job_rows if type(o).__name__ == "Job")
        # 标签推导：全部同校同课 → job 继承
        assert job.school_id == school
        assert job.course_id == course
        # JobUpload 关联建立
        assert any(type(o) is JobUpload for o in job_rows)

    async def test_imported_upload_accepted(self, register_spy) -> None:
        user, owner = uuid.uuid4(), uuid.uuid4()
        upload = _upload(owner)  # 他人文件
        session = FakeCreateSession([upload], [upload.id])  # 已保存引用

        try:
            async with _make_client(session, user) as http:
                res = await http.post(
                    "/api/v1/jobs", json={"upload_ids": [str(upload.id)]}
                )
        finally:
            _cleanup()

        assert res.status_code == 202

    async def test_unowned_without_import_rejected(self, register_spy) -> None:
        user, owner = uuid.uuid4(), uuid.uuid4()
        upload = _upload(owner)
        session = FakeCreateSession([upload], [])  # 无引用授权

        try:
            async with _make_client(session, user) as http:
                res = await http.post(
                    "/api/v1/jobs", json={"upload_ids": [str(upload.id)]}
                )
        finally:
            _cleanup()

        assert res.status_code == 404
        assert res.json()["error_code"] == "JOB_NOT_FOUND"
        assert not register_spy

    async def test_deleted_upload_rejected(self, register_spy) -> None:
        user = uuid.uuid4()
        upload = _upload(user, deleted_at=datetime.now(UTC))
        session = FakeCreateSession([upload], [upload.id])

        try:
            async with _make_client(session, user) as http:
                res = await http.post(
                    "/api/v1/jobs", json={"upload_ids": [str(upload.id)]}
                )
        finally:
            _cleanup()

        assert res.status_code == 404
        assert not register_spy

    async def test_not_parsed_upload_rejected(self, register_spy) -> None:
        user = uuid.uuid4()
        upload = _upload(user, parse_status="parsing")
        session = FakeCreateSession([upload], [])

        try:
            async with _make_client(session, user) as http:
                res = await http.post(
                    "/api/v1/jobs", json={"upload_ids": [str(upload.id)]}
                )
        finally:
            _cleanup()

        assert res.status_code == 409
        assert res.json()["error_code"] == "UPLOAD_NOT_PARSED"
        assert not register_spy

    async def test_mixed_school_tags_derive_none(self, register_spy) -> None:
        user = uuid.uuid4()
        u1 = _upload(user, school_id=uuid.uuid4())
        u2 = _upload(user, school_id=uuid.uuid4())
        session = FakeCreateSession([u1, u2], [])
        job_rows: list[Any] = []
        orig_add = session.add

        def _track(obj: Any) -> None:
            job_rows.append(obj)
            orig_add(obj)

        session.add = _track  # type: ignore[method-assign]

        try:
            async with _make_client(session, user) as http:
                res = await http.post(
                    "/api/v1/jobs", json={"upload_ids": [str(u1.id), str(u2.id)]}
                )
        finally:
            _cleanup()

        assert res.status_code == 202
        job = next(o for o in job_rows if type(o).__name__ == "Job")
        assert job.school_id is None
        assert job.course_id is None

    async def test_empty_payload_rejected(self, register_spy) -> None:
        user = uuid.uuid4()
        session = FakeCreateSession([], [])

        try:
            async with _make_client(session, user) as http:
                res = await http.post("/api/v1/jobs", json={"upload_ids": []})
        finally:
            _cleanup()

        assert res.status_code == 400
        assert res.json()["error_code"] == "INPUT_EMPTY"
        assert not register_spy
