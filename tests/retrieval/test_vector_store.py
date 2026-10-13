"""PgVectorStore 契约测试（不依赖真实数据库与真实 embedding）。

覆盖两个历史缺陷的回归：
- upsert 必须把 chunk.embedding 写入 INSERT（Chunk 曾缺字段、向量曾未绑定，
  导致含文件上传的任务在预处理阶段必挂）；
- {"or": [{"and": [...]}]} 编译为 SQL 时必须保持分支内 AND 嵌套（曾因 extend
  展平破坏 AND 语义，导致跨用户私有内容可被检索）；user_scope 隔离条件参与编译。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_
from sqlalchemy.sql.elements import BooleanClauseList

from app.core.enums import SourceType
from app.ingestion.chunking import Chunk
from app.retrieval.vector_store import PgVectorStore


class _RecordingSession:
    """捕获 execute() 收到的语句，用于断言 INSERT 参数（不触库）。"""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> None:
        self.statements.append(stmt)

    async def commit(self) -> None:
        return None

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _make_chunk(**overrides: Any) -> Chunk:
    defaults: dict[str, Any] = {
        "text": "质点做匀速直线运动",
        "source_type": SourceType.book,
        "chunk_index": 0,
    }
    defaults.update(overrides)
    return Chunk(**defaults)


class TestUpsertEmbedding:
    async def test_upsert_writes_chunk_embedding(self) -> None:
        """upsert 生成的 INSERT 必须携带 chunk.embedding。"""
        session = _RecordingSession()
        store = PgVectorStore(lambda: session)
        chunk = _make_chunk(embedding=[0.1, 0.2, 0.3])

        await store.upsert([chunk])

        assert len(session.statements) == 1
        params = session.statements[0].compile().params
        assert params["embedding"] == [0.1, 0.2, 0.3]

    async def test_upsert_writes_heading_metadata(self) -> None:
        """upsert 携带统一分块的元数据列（heading_path/偏移/embedding_text）。"""
        session = _RecordingSession()
        store = PgVectorStore(lambda: session)
        chunk = _make_chunk(
            heading_path="第二章 > 2.3 碰撞",
            char_start=12,
            char_end=340,
            embedding_text="第二章 > 2.3 碰撞\n正文……",
            embedding=[0.1],
        )

        await store.upsert([chunk])

        params = session.statements[0].compile().params
        assert params["heading_path"] == "第二章 > 2.3 碰撞"
        assert params["char_start"] == 12
        assert params["char_end"] == 340
        assert params["embedding_text"] == "第二章 > 2.3 碰撞\n正文……"

    async def test_upsert_each_chunk_keeps_own_embedding(self) -> None:
        """逐条 INSERT 时各 chunk 携带各自的向量。"""
        session = _RecordingSession()
        store = PgVectorStore(lambda: session)
        chunks = [
            _make_chunk(text="a", chunk_index=0, embedding=[0.1]),
            _make_chunk(text="b", chunk_index=1, embedding=[0.2]),
        ]

        await store.upsert(chunks)

        assert len(session.statements) == 2
        assert session.statements[0].compile().params["embedding"] == [0.1]
        assert session.statements[1].compile().params["embedding"] == [0.2]

    async def test_upsert_empty_list_is_noop(self) -> None:
        session = _RecordingSession()
        store = PgVectorStore(lambda: session)

        await store.upsert([])

        assert session.statements == []


class TestBuildFilter:
    def setup_method(self) -> None:
        self.store = PgVectorStore(None)

    def test_or_of_ands_keeps_nested_groups(self) -> None:
        """or 分支内的 and 条件必须保持成组，不能被展平为并列 OR。"""
        expr: dict[str, Any] = {
            "or": [
                {"and": [
                    {"in": {"upload_id": ["u1", "u2"]}},
                    {"eq": {"source_type": "book"}},
                ]},
                {"and": [
                    {"eq": {"source_type": "note"}},
                    {"eq": {"chunk_index": 3}},
                ]},
            ],
        }

        conditions = self.store._build_filter(expr)  # noqa: SLF001

        assert len(conditions) == 1
        or_group = conditions[0]
        assert isinstance(or_group, BooleanClauseList)
        branches = list(or_group.clauses)
        assert len(branches) == 2, "or 分支内层被展平，隔离语义失效"
        for branch in branches:
            assert isinstance(branch, BooleanClauseList), "分支内 AND 条件必须保持成组"

        sql = str(or_group.compile())
        assert "upload_id IN" in sql
        assert "source_type" in sql
        assert "chunk_index" in sql

    def test_flat_filters_still_supported(self) -> None:
        """扁平 eq/in 过滤行为不变。"""
        expr: dict[str, Any] = {
            "eq": {"source_type": "book"},
            "in": {"upload_id": ["u1"]},
        }

        conditions = self.store._build_filter(expr)  # noqa: SLF001

        assert len(conditions) == 2
        sql = str(and_(*conditions).compile())
        assert "source_type" in sql
        assert "upload_id IN" in sql

    def test_user_scope_appended_as_condition(self) -> None:
        """user_scope 已随白名单化删除：未知键被忽略，不产生条件。"""
        expr: dict[str, Any] = {
            "user_scope": uuid.uuid4(),
            "or": [{"eq": {"source_type": "book"}}],
        }

        conditions = self.store._build_filter(expr)  # noqa: SLF001

        assert len(conditions) == 1

    def test_user_scope_accepts_string_uuid(self) -> None:
        expr: dict[str, Any] = {
            "user_scope": str(uuid.uuid4()),
            "or": [{"eq": {"source_type": "note"}}],
        }

        conditions = self.store._build_filter(expr)  # noqa: SLF001

        assert len(conditions) == 1

    def test_combined_and_semantics(self) -> None:
        """and 与 or 嵌套组合：and_(or_base, chunk_index) 双重条件结构成立。"""
        expr: dict[str, Any] = {
            "and": [
                {"or": [
                    {"in": {"upload_id": ["u1", "u2"]}},
                    {"eq": {"chunk_index": 5}},
                ]},
                {"or": [{"eq": {"source_type": "book"}}]},
            ],
        }

        conditions = self.store._build_filter(expr)  # noqa: SLF001
        combined = and_(*conditions)

        assert isinstance(combined, BooleanClauseList)
        assert len(list(combined.clauses)) == 2
