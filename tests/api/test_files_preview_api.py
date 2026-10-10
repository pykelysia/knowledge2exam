"""GET /files/{id}/preview 端点测试：文本/文件双路径、截断、鉴权、异常。

覆盖增量审查发现的 CRITICAL 回归：文本类路径曾因 try/except 缩进错误
触发 UnboundLocalError → 500。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.api import uploads as uploads_module
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.enums import SourceType, Visibility
from app.main import app
from app.models.upload import Upload


class FakePreviewSession:
    """get 按模型分发；scalar 供 FileImport 查询（默认无引用）。"""

    def __init__(self, upload: Upload) -> None:
        self.upload = upload
        self.scalar_values: list[Any] = []

    async def get(self, model: type, pk: uuid.UUID) -> Any:  # noqa: ARG002
        if model is Upload:
            return self.upload if self.upload.id == pk else None
        return None

    async def scalar(self, stmt: Any) -> Any:  # noqa: ARG002
        if self.scalar_values:
            return self.scalar_values.pop(0)
        return None


def _make_upload(**kwargs: Any) -> Upload:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "source_type": SourceType.book,
        "filename": "textbook.pdf",
        "visibility": Visibility.private,
        "parse_status": "succeeded",
        "school_id": None,
        "course_id": None,
    }
    defaults.update(kwargs)
    return Upload(**defaults)


def _client(session: FakePreviewSession, user_id: uuid.UUID) -> AsyncClient:
    async def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: type(
        "U", (), {"id": user_id, "school_id": None}
    )()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _cleanup() -> None:
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


class TestPreviewFile:
    async def test_text_path_returns_raw_text(self, monkeypatch: Any) -> None:
        """CRITICAL 回归：文本类（manual_text）预览必须走 raw_text 而非读产物。"""
        upload = _make_upload(
            source_type=SourceType.manual_text,
            filename=None,
            raw_text="手写笔记内容",
        )
        session = FakePreviewSession(upload)

        async def _storage_get_must_not_be_called(key: str) -> bytes:
            raise AssertionError("文本类预览不得读取对象存储")

        monkeypatch.setattr(uploads_module.storage, "get", _storage_get_must_not_be_called)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.get(f"/api/v1/files/{upload.id}/preview")
        finally:
            _cleanup()

        assert res.status_code == 200
        body = res.json()
        assert body["text"] == "手写笔记内容"
        assert body["truncated"] is False

    async def test_file_path_reads_parsed_artifact(self, monkeypatch: Any) -> None:
        upload = _make_upload(
            parse_status="succeeded",
            parsed_key=f"uploads/{uuid.uuid4()}/parsed/text.json",
        )
        session = FakePreviewSession(upload)

        async def _fake_get(key: str) -> bytes:
            assert key == upload.parsed_key
            return json.dumps("解析后的全文").encode("utf-8")

        monkeypatch.setattr(uploads_module.storage, "get", _fake_get)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.get(f"/api/v1/files/{upload.id}/preview")
        finally:
            _cleanup()

        assert res.status_code == 200
        assert res.json()["text"] == "解析后的全文"

    async def test_missing_artifact_returns_409(self, monkeypatch: Any) -> None:
        upload = _make_upload(
            parse_status="succeeded", parsed_key="uploads/x/parsed/text.json"
        )
        session = FakePreviewSession(upload)

        async def _boom(key: str) -> bytes:
            raise FileNotFoundError(key)

        monkeypatch.setattr(uploads_module.storage, "get", _boom)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.get(f"/api/v1/files/{upload.id}/preview")
        finally:
            _cleanup()

        assert res.status_code == 409
        assert res.json()["error_code"] == "UPLOAD_NOT_PARSED"

    async def test_not_parsed_returns_409(self) -> None:
        upload = _make_upload(parse_status="parsing")
        session = FakePreviewSession(upload)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.get(f"/api/v1/files/{upload.id}/preview")
        finally:
            _cleanup()

        assert res.status_code == 409

    async def test_private_file_of_others_returns_404(self) -> None:
        upload = _make_upload(visibility=Visibility.private)
        session = FakePreviewSession(upload)  # scalar → None：无引用授权

        try:
            async with _client(session, uuid.uuid4()) as http:
                res = await http.get(f"/api/v1/files/{upload.id}/preview")
        finally:
            _cleanup()

        assert res.status_code == 404

    async def test_public_file_previewable_by_others(self, monkeypatch: Any) -> None:
        upload = _make_upload(
            visibility=Visibility.public,
            source_type=SourceType.manual_text,
            filename=None,
            raw_text="公开的文本",
        )
        session = FakePreviewSession(upload)

        async def _no_storage(key: str) -> bytes:
            raise AssertionError("文本类预览不得读取对象存储")

        monkeypatch.setattr(uploads_module.storage, "get", _no_storage)

        try:
            async with _client(session, uuid.uuid4()) as http:
                res = await http.get(f"/api/v1/files/{upload.id}/preview")
        finally:
            _cleanup()

        assert res.status_code == 200
        assert res.json()["text"] == "公开的文本"

    async def test_long_text_truncated_to_limit(self, monkeypatch: Any) -> None:
        upload = _make_upload(
            source_type=SourceType.manual_text,
            filename=None,
            raw_text="长" * 100,
        )
        session = FakePreviewSession(upload)
        monkeypatch.setattr(uploads_module.settings, "max_preview_chars", 10)

        try:
            async with _client(session, upload.user_id) as http:
                res = await http.get(f"/api/v1/files/{upload.id}/preview")
        finally:
            _cleanup()

        assert res.status_code == 200
        body = res.json()
        assert body["text"] == "长" * 10
        assert body["truncated"] is True
