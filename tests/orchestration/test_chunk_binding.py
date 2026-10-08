"""解析管线 chunk 绑定回归测试：上传件元信息与嵌入向量绑定到 chunk。

历史缺陷：stages 中 zip(chunks, embeddings) 用 _emb 丢弃了向量，Chunk 又没有
embedding 字段，PgVectorStore.upsert 读 chunk.embedding 抛 AttributeError。
该逻辑已随「上传即解析」迁移到 app/ingestion/pipeline._bind_chunk_metadata。
"""

from __future__ import annotations

import uuid

import pytest

from app.core.enums import SourceType
from app.ingestion.chunking import Chunk
from app.ingestion.pipeline import _bind_chunk_metadata
from app.models.upload import Upload


def _make_upload(source_type: SourceType = SourceType.book) -> Upload:
    return Upload(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        source_type=source_type,
        filename="textbook.pdf",
        storage_key=f"uploads/{uuid.uuid4()}.pdf",
    )


def test_metadata_and_embedding_bound_to_chunks() -> None:
    upload = _make_upload()
    chunks = [Chunk(text="a"), Chunk(text="b"), Chunk(text="c")]
    embeddings = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]

    _bind_chunk_metadata(chunks, embeddings, upload=upload)

    for chunk, expected in zip(chunks, embeddings, strict=True):
        assert chunk.embedding == expected, "嵌入向量必须绑定到 chunk"
        assert chunk.upload_id == upload.id
        assert chunk.source_type == upload.source_type


def test_length_mismatch_raises() -> None:
    """zip 严格等长：向量数与 chunk 数不一致必须抛错而非静默错配。"""
    upload = _make_upload()

    with pytest.raises(ValueError):
        _bind_chunk_metadata([Chunk(text="a")], [[0.0], [1.0]], upload=upload)


def test_no_legacy_scope_fields_bound() -> None:
    """chunk 不再绑定归属/可见性冗余字段（检索走 upload 白名单）。"""
    upload = _make_upload()
    chunks = [Chunk(text="x")]

    _bind_chunk_metadata(chunks, [[0.0]], upload=upload)

    assert not hasattr(chunks[0], "is_shared")
    assert not hasattr(chunks[0], "user_id")
    assert not hasattr(chunks[0], "school_id")
