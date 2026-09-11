"""FilterBuilder 单元测试。"""

from __future__ import annotations

import uuid

from app.core.enums import SourceType
from app.retrieval.filters import FilterBuilder


class TestFilterBuilder:
    def test_additive(self) -> None:
        uid = uuid.uuid4()
        sid = uuid.uuid4()
        cid = uuid.uuid4()
        upload_ids = [uuid.uuid4(), uuid.uuid4()]

        expr = FilterBuilder.additive(
            user_id=uid,
            school_id=sid,
            course_id=cid,
            source_type=SourceType.book,
            upload_ids=upload_ids,
        )
        assert "or" in expr
        assert len(expr["or"]) == 2

    def test_exclusive_with_uploads(self) -> None:
        upload_ids = [uuid.uuid4()]
        expr = FilterBuilder.exclusive(
            user_id=uuid.uuid4(),
            school_id=uuid.uuid4(),
            course_id=uuid.uuid4(),
            source_type=SourceType.keypoint_list,
            upload_ids=upload_ids,
        )
        assert "and" in expr
        assert {"in": {"upload_id": upload_ids, "eq": {"source_type": "keypoint_list"}}}

    def test_exclusive_without_uploads(self) -> None:
        sid = uuid.uuid4()
        expr = FilterBuilder.exclusive(
            user_id=uuid.uuid4(),
            school_id=sid,
            course_id=uuid.uuid4(),
            source_type=SourceType.keypoint_list,
            upload_ids=[],
        )
        assert "and" in expr
        conds = expr["and"]
        assert {"eq": {"school_id": sid}} in conds

    def test_user_scope(self) -> None:
        uid = uuid.uuid4()
        expr = FilterBuilder.user_scope(uid)
        assert "or" in expr

    def test_merge(self) -> None:
        f1 = FilterBuilder.user_scope(uuid.uuid4())
        f2 = {"and": [{"eq": {"source_type": "book"}}]}
        merged = FilterBuilder.merge(f1, f2)
        assert "and" in merged
        assert len(merged["and"]) == 2
