"""_bind_chunk_metadata 回归测试：资源元信息与嵌入向量绑定到 chunk。

历史缺陷：stages 中 zip(chunks, embeddings) 用 _emb 丢弃了向量，Chunk 又没有
embedding 字段，PgVectorStore.upsert 读 chunk.embedding 抛 AttributeError，
导致任何含 book/lecture/note/keypoint_list 上传的任务预处理必挂。
"""

from __future__ import annotations

import uuid

from app.core.enums import SourceType
from app.ingestion.chunking import Chunk
from app.models.job import Job
from app.models.resource import Resource
from app.models.upload import Upload
from app.orchestration.stages import _bind_chunk_metadata


def _make_fixtures(school: uuid.UUID | None, course: uuid.UUID | None, shareable: bool = True):
    user = uuid.uuid4()
    job = Job(user_id=user, school_id=school, course_id=course)
    upload = Upload(
        id=uuid.uuid4(),
        user_id=user,
        source_type=SourceType.book,
        shareable=shareable,
        filename="textbook.pdf",
        storage_key=f"uploads/{uuid.uuid4()}",
    )
    resource = Resource(
        id=uuid.uuid4(),
        upload_id=upload.id,
        source_type=SourceType.book,
        school_id=school,
        course_id=course,
        char_count=1200,
    )
    return job, upload, resource


def test_metadata_and_embedding_bound_to_chunks() -> None:
    school, course = uuid.uuid4(), uuid.uuid4()
    job, upload, resource = _make_fixtures(school, course)
    chunks = [Chunk(text="a"), Chunk(text="b"), Chunk(text="c")]
    embeddings = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]

    _bind_chunk_metadata(chunks, embeddings, resource=resource, upload=upload, job=job)

    for chunk, expected in zip(chunks, embeddings, strict=True):
        assert chunk.embedding == expected, "嵌入向量必须绑定到 chunk"
        assert chunk.resource_id == resource.id
        assert chunk.upload_id == upload.id
        assert chunk.user_id == job.user_id
        assert chunk.school_id == school
        assert chunk.course_id == course
        assert chunk.source_type == SourceType.book
        assert chunk.is_shared is True


def test_shared_flag_requires_school_and_course() -> None:
    job, upload, resource = _make_fixtures(None, None)
    chunks = [Chunk(text="x")]

    _bind_chunk_metadata(chunks, [[0.0]], resource=resource, upload=upload, job=job)

    assert chunks[0].is_shared is False
    assert chunks[0].embedding == [0.0]
    assert chunks[0].school_id is None


def test_unshareable_upload_never_shared() -> None:
    job, upload, resource = _make_fixtures(uuid.uuid4(), uuid.uuid4(), shareable=False)
    chunks = [Chunk(text="x")]

    _bind_chunk_metadata(chunks, [[0.0]], resource=resource, upload=upload, job=job)

    assert chunks[0].is_shared is False
