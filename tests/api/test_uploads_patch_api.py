"""PATCH /uploads/{id} 端点测试：visibility 语义、标签校验、越权。

假会话 + 依赖覆盖，不触真实数据库。覆盖审查发现的 HIGH 缺陷：
显式 visibility=null 曾直接落库触发 IntegrityError → 500，现应 400。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.enums import SourceType, Visibility
from app.main import app
from app.models.catalog import Course, School
from app.models.upload import Upload

_SCHOOL = uuid.uuid4()
_COURSE = uuid.uuid4()


class FakePatchSession:
    """按模型类型分发 db.get 的最小 AsyncSession 假实现。"""

    def __init__(self, upload: Upload) -> None:
        self.upload = upload
        self.committed = 0

    async def get(self, model: type, pk: uuid.UUID) -> Any:
        if model is Upload:
            return self.upload if self.upload.id == pk else None
        if model is School:
            return School(id=pk, name="某大学") if pk == _SCHOOL else None
        if model is Course:
            return Course(id=pk, name="信号与系统") if pk == _COURSE else None
        return None

    async def commit(self) -> None:
        self.committed += 1

    async def rollback(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        pass


def _make_upload(**kwargs: Any) -> Upload:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "source_type": SourceType.note,
        "filename": "note.md",
        "visibility": Visibility.private,
        "parse_status": "succeeded",
        "school_id": None,
        "course_id": None,
    }
    defaults.update(kwargs)
    return Upload(**defaults)


def _client(session: FakePatchSession, user_id: uuid.UUID) -> AsyncClient:
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


class TestPatchUpload:
    async def test_explicit_visibility_null_rejected(self) -> None:
        """HIGH 回归：显式 visibility=null 必须 400，而非落库 500。"""
        upload = _make_upload()
        session = FakePatchSession(upload)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}", json={"visibility": None}
                )
        finally:
            _cleanup()

        assert res.status_code == 400
        assert res.json()["error_code"] == "INPUT_EMPTY"
        assert session.committed == 0
        assert upload.visibility == Visibility.private

    async def test_visibility_update(self) -> None:
        upload = _make_upload()
        session = FakePatchSession(upload)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}", json={"visibility": "public"}
                )
        finally:
            _cleanup()

        assert res.status_code == 200
        body = res.json()
        assert body["visibility"] == "public"
        assert upload.visibility == Visibility.public

    async def test_school_visibility_requires_school_tag(self) -> None:
        upload = _make_upload(school_id=None)
        session = FakePatchSession(upload)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}", json={"visibility": "school"}
                )
        finally:
            _cleanup()

        assert res.status_code == 400
        assert res.json()["error_code"] == "SHARE_SCOPE_REQUIRED"

    async def test_school_visibility_with_tag_accepted(self) -> None:
        upload = _make_upload(school_id=_SCHOOL)
        session = FakePatchSession(upload)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}", json={"visibility": "school"}
                )
        finally:
            _cleanup()

        assert res.status_code == 200
        assert upload.visibility == Visibility.school

    async def test_text_upload_visibility_forbidden(self) -> None:
        upload = _make_upload(
            source_type=SourceType.manual_text,
            raw_text="内容",
            filename=None,
        )
        session = FakePatchSession(upload)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}", json={"visibility": "public"}
                )
        finally:
            _cleanup()

        assert res.status_code == 400
        assert res.json()["error_code"] == "SHARE_NOT_ALLOWED"

    async def test_non_owner_rejected(self) -> None:
        upload = _make_upload()
        session = FakePatchSession(upload)

        try:
            async with _client(session, uuid.uuid4()) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}", json={"visibility": "public"}
                )
        finally:
            _cleanup()

        assert res.status_code == 404

    async def test_deleted_upload_rejected(self) -> None:
        upload = _make_upload(deleted_at=datetime.now(UTC))
        session = FakePatchSession(upload)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}", json={"visibility": "public"}
                )
        finally:
            _cleanup()

        assert res.status_code == 404

    async def test_tag_update_and_clear(self) -> None:
        upload = _make_upload()
        session = FakePatchSession(upload)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}",
                    json={"school_id": str(_SCHOOL), "course_id": str(_COURSE)},
                )
        finally:
            _cleanup()

        assert res.status_code == 200
        assert upload.school_id == _SCHOOL
        assert upload.course_id == _COURSE

        # 显式 null 清除标签
        try:
            async with _client(session, upload.user_id) as http:
                res = await http.patch(
                    f"/api/v1/uploads/{upload.id}",
                    json={"school_id": None, "course_id": None},
                )
        finally:
            _cleanup()

        assert res.status_code == 200
        assert upload.school_id is None
        assert upload.course_id is None
