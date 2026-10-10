"""上传解析管线状态机测试：成功 / 失败 / 软删除跳过 / 文本即时登记 / 原子收敛。

parse_upload 内部通过 AsyncSessionLocal 开会话；此处用假会话工厂供给主会话，
其余外部依赖（storage / 嵌入 / 向量库）全部打桩。
收敛采用条件 UPDATE（WHERE deleted_at IS NULL），rowcount 可脚本化。
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

_EXPECTED_WARNINGS = [{"code": "OCR_DEGRADED", "message": "第 1 页降级"}]


class FakeExecResult:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


class FakeSession:
    """最小 AsyncSession：get 返回固定对象，execute 按脚本出队 rowcount。"""

    def __init__(
        self,
        get_value: Any = None,
        exec_rowcounts: list[int] | None = None,
    ) -> None:
        self._get_value = get_value
        self._exec_rowcounts = list(exec_rowcounts or [])
        self.executed: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model: type, pk: Any) -> Any:  # noqa: ARG002
        return self._get_value

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(self, stmt: Any) -> FakeExecResult:  # noqa: ARG002
        self.executed.append(stmt)
        rowcount = self._exec_rowcounts.pop(0) if self._exec_rowcounts else 1
        return FakeExecResult(rowcount=rowcount)


class _Ctx:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, sessions: list[FakeSession]) -> None:
        self._sessions = list(sessions)
        self.created: list[FakeSession] = []

    @property
    def sessions(self) -> list[FakeSession]:
        """尚未取出的会话队列（测试预置 rowcount 用）。"""
        return self._sessions

    def __call__(self) -> _Ctx:
        session = self._sessions.pop(0)
        self.created.append(session)
        return _Ctx(session)


class FakeEmbedder:
    @classmethod
    def from_settings(cls) -> FakeEmbedder:
        return cls()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class FakeVectorStore:
    instances: list[FakeVectorStore] = []

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


async def _parse_with_warning(u: Upload, warnings: list[dict]) -> tuple[str, int]:
    warnings.append(_EXPECTED_WARNINGS[0])
    return "全文内容", 4


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    factory: FakeSessionFactory,
    *,
    parse_file: Any = None,
    persistable: bool = True,
) -> None:
    """打桩公共依赖：会话工厂 / 解析函数 / storage / 嵌入 / 向量库。

    persistable=False 时条件 UPDATE rowcount=0（模拟软删除已提交）。
    """

    async def _fake_put(key: str, data: bytes) -> None:
        pass

    monkeypatch.setattr(pipeline, "AsyncSessionLocal", factory)
    monkeypatch.setattr(pipeline, "_parse_file", parse_file or _parse_with_warning)
    monkeypatch.setattr(pipeline.storage, "put", _fake_put)
    monkeypatch.setattr(pipeline, "EmbeddingClient", FakeEmbedder)
    monkeypatch.setattr(pipeline, "PgVectorStore", FakeVectorStore)
    # 预置条件 UPDATE 的 rowcount（0=行已被软删除）
    main = factory.sessions[0] if factory.sessions else factory.created[0]
    if not persistable:
        main._exec_rowcounts = [0]


@pytest.fixture(autouse=True)
def _reset_vs() -> None:
    FakeVectorStore.instances.clear()


class TestParseUpload:
    async def test_success_flow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        upload = _make_upload()
        main = FakeSession(get_value=upload)
        factory = FakeSessionFactory([main])
        _patch_common(monkeypatch, factory)

        await pipeline.parse_upload(upload.id)

        # 收敛经条件 UPDATE 完成：值经语句参数断言（ORM 对象不再就地赋值）
        assert main.executed, "应执行条件 UPDATE"
        params = main.executed[-1].compile().params
        assert params["parse_status"] == "succeeded"
        assert params["parsed_key"] == f"uploads/{upload.id}/parsed/text.json"
        assert params["char_count"] == 4
        assert params["parse_warnings"] == _EXPECTED_WARNINGS
        assert main.commits >= 1
        # book 类型走向量化：chunk 绑定 upload_id 并写入向量库
        assert FakeVectorStore.instances
        chunks = FakeVectorStore.instances[-1].uploaded_chunks
        assert chunks and all(c.upload_id == upload.id for c in chunks)

    async def test_parse_failure_marks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        upload = _make_upload()
        main = FakeSession(get_value=upload)
        factory = FakeSessionFactory([main])

        async def _boom(u: Upload, warnings: list[dict]) -> tuple[str, int]:
            raise RuntimeError("解析器崩溃")

        _patch_common(monkeypatch, factory, parse_file=_boom)

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

        _patch_common(monkeypatch, factory, parse_file=_should_not_run)

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

        _patch_common(monkeypatch, factory, parse_file=_should_not_run)

        await pipeline.parse_upload(upload.id)

        assert called == []
        assert upload.parse_status == "succeeded"
        assert upload.char_count == len("手写笔记内容")

    async def test_converge_discards_result_when_deleted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """条件 UPDATE rowcount=0（软删除已提交）：产物与 chunk 整体回滚。"""
        upload = _make_upload()
        main = FakeSession(get_value=upload)
        factory = FakeSessionFactory([main])
        deleted_keys: list[str] = []

        async def _fake_delete(key: str) -> None:
            deleted_keys.append(key)

        _patch_common(monkeypatch, factory, persistable=False)
        monkeypatch.setattr(pipeline.storage, "delete", _fake_delete)

        await pipeline.parse_upload(upload.id)

        assert main.rollbacks >= 1
        # 仅允许初始 parsing 状态的那次提交；产物收敛不得提交
        assert main.commits == 1
        # 已落盘的解析产物被尽力清理（防孤儿文件）
        assert deleted_keys == [f"uploads/{upload.id}/parsed/text.json"]
        # ORM 对象状态不应被就地改为 succeeded（收敛仅经条件 UPDATE）
        assert upload.parse_status == "parsing"
