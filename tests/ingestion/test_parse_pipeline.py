"""上传解析管线状态机测试：成功 / 失败 / 软删除跳过 / 文本即时登记 / 删除检查点。

parse_upload 内部通过 AsyncSessionLocal 开会话；此处用假会话工厂按顺序
供给主会话与检查点会话，其余外部依赖（storage / 嵌入 / 向量库）全部打桩。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

import app.ingestion.pipeline as pipeline
from app.core.enums import SourceType
from app.ingestion.chunking import Chunk
from app.models.upload import Upload


class FakeSession:
    """最小 AsyncSession：get 返回固定对象，scalar 按脚本出队。"""

    def __init__(
        self,
        get_value: Any = None,
        scalar_values: list[Any] | None = None,
    ) -> None:
        self._get_value = get_value
        self._scalar_values = list(scalar_values or [])
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model: type, pk: Any) -> Any:  # noqa: ARG002
        return self._get_value

    async def scalar(self, stmt: Any) -> Any:  # noqa: ARG002
        if self._scalar_values:
            return self._scalar_values.pop(0)
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(self, stmt: Any) -> None:  # noqa: ARG002
        pass


class _Ctx:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeSessionFactory:
    """按创建顺序返回预置会话，记录每次会话用途。"""

    def __init__(self, sessions: list[FakeSession]) -> None:
        self._sessions = list(sessions)
        self.created: list[FakeSession] = []

    def __call__(self) -> _Ctx:
        session = self._sessions.pop(0)
        self.created.append(session)
        return _Ctx(session)


class FakeEmbedder:
    @classmethod
    def from_settings(cls) -> "FakeEmbedder":
        return cls()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class FakeVectorStore:
    instances: list["FakeVectorStore"] = []

    def __init__(self, session_factory: Any) -> None:  # noqa: ARG002
        self.uploaded_chunks: list[Chunk] = []
        FakeVectorStore.instances.append(self)

    async def upsert(self, chunks: list[Chunk], *, session: Any = None) -> None:  # noqa: ARG002
        self.uploaded_chunks.extend(chunks)


def _make_upload(
    source_type: SourceType = SourceType.book,
    *,
    deleted_at: datetime | None = None,
    parse_status: str = "pending",
    raw_text: str | None = None,
) -> Upload:
    return Upload(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        source_type=source_type,
        filename="textbook.pdf",
        storage_key="uploads/x/textbook.pdf",
        raw_text=raw_text,
        parse_status=parse_status,
        deleted_at=deleted_at,
    )


@pytest.fixture(autouse=True)
def _reset_vs() -> None:
    FakeVectorStore.instances.clear()


class TestParseUpload:
    async def test_success_flow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        upload = _make_upload()
        main = FakeSession(get_value=upload)
        checkpoint = FakeSession(scalar_values=[None])  # 未删除
        factory = FakeSessionFactory([main, checkpoint])

        parse_calls: list[Upload] = []
        puts: list[str] = []

        async def _fake_parse_file(u: Upload, warnings: list[dict]) -> tuple[str, int]:
            parse_calls.append(u)
            warnings.append({"code": "OCR_DEGRADED", "message": "第 1 页降级"})
            return "全文内容", 4

        async def _fake_put(key: str, data: bytes) -> None:
            puts.append(key)

        monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
        monkeypatch.setattr(pipeline, "_parse_file", _fake_parse_file)
        monkeypatch.setattr(pipeline.storage, "put", _fake_put)
        monkeypatch.setattr(pipeline, "EmbeddingClient", FakeEmbedder)
        monkeypatch.setattr(pipeline, "PgVectorStore", FakeVectorStore)

        await pipeline.parse_upload(upload.id)

        assert parse_calls == [upload]
        assert upload.parse_status == "succeeded"
        assert upload.char_count == 4
        assert upload.parsed_key == f"uploads/{upload.id}/parsed/text.json"
        assert puts == [upload.parsed_key]
        assert upload.parse_warnings == [{"code": "OCR_DEGRADED", "message": "第 1 页降级"}]
        # book 类型走向量化：chunk 绑定 upload_id 并写入向量库
        assert FakeVectorStore.instances
        chunks = FakeVectorStore.instances[0].uploaded_chunks
        assert chunks and all(c.upload_id == upload.id for c in chunks)
        assert main.commits >= 1

    async def test_parse_failure_marks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        upload = _make_upload()
        main = FakeSession(get_value=upload)
        factory = FakeSessionFactory([main])

        async def _boom(u: Upload, warnings: list[dict]) -> tuple[str, int]:
            raise RuntimeError("解析器崩溃")

        monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
        monkeypatch.setattr(pipeline, "_parse_file", _boom)

        await pipeline.parse_upload(upload.id)

        assert upload.parse_status == "failed"
        assert upload.parse_error == "解析器崩溃"

    async def test_deleted_upload_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        upload = _make_upload(deleted_at=datetime.now(UTC))
        factory = FakeSessionFactory([FakeSession(get_value=upload)])
        called: list[uuid.UUID] = []

        async def _should_not_run(u: Upload, warnings: list[dict]) -> tuple[str, int]:
            called.append(u.id)
            return "", 0

        monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
        monkeypatch.setattr(pipeline, "_parse_file", _should_not_run)

        await pipeline.parse_upload(upload.id)

        assert called == []
        assert upload.parse_status == "pending"

    async def test_text_upload_instant_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        upload = _make_upload(SourceType.manual_text, raw_text="手写笔记内容")
        factory = FakeSessionFactory([FakeSession(get_value=upload)])
        called: list[uuid.UUID] = []

        async def _should_not_run(u: Upload, warnings: list[dict]) -> tuple[str, int]:
            called.append(u.id)
            return "", 0

        monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
        monkeypatch.setattr(pipeline, "_parse_file", _should_not_run)

        await pipeline.parse_upload(upload.id)

        assert called == []
        assert upload.parse_status == "succeeded"
        assert upload.char_count == len("手写笔记内容")

    async def test_deletion_checkpoint_discards_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """检查点发现软删除：产物不入库（rollback），状态不收敛为 succeeded。"""
        upload = _make_upload()
        main = FakeSession(get_value=upload)
        checkpoint = FakeSession(scalar_values=[datetime.now(UTC)])  # 已删除
        factory = FakeSessionFactory([main, checkpoint])

        async def _fake_parse(u: Upload, warnings: list[dict]) -> tuple[str, int]:
            return "全文", 2

        async def _fake_put(key: str, data: bytes) -> None:
            pass

        monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
        monkeypatch.setattr(pipeline, "_parse_file", _fake_parse)
        monkeypatch.setattr(pipeline.storage, "put", _fake_put)
        monkeypatch.setattr(pipeline, "EmbeddingClient", FakeEmbedder)
        monkeypatch.setattr(pipeline, "PgVectorStore", FakeVectorStore)

        await pipeline.parse_upload(upload.id)

        assert main.rollbacks >= 1
        assert upload.parse_status != "succeeded"
