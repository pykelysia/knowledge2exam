"""FilterBuilder 单元测试。"""

from __future__ import annotations

import uuid

from app.core.enums import SourceType
from app.retrieval.filters import FilterBuilder


class TestFilterBuilder:
    def test_additive_with_uploads(self) -> None:
        """有上传件：本次上传 OR 同校同课程共享库。"""
        sid = uuid.uuid4()
        cid = uuid.uuid4()
        upload_ids = [uuid.uuid4(), uuid.uuid4()]

        expr = FilterBuilder.additive(
            school_id=sid,
            course_id=cid,
            source_type=SourceType.book,
            upload_ids=upload_ids,
        )

        assert "or" in expr
        assert len(expr["or"]) == 2
        upload_branch = expr["or"][0]["and"]
        assert {"in": {"upload_id": [str(u) for u in upload_ids]}} in upload_branch
        shared_branch = expr["or"][1]["and"]
        assert {"eq": {"school_id": str(sid)}} in shared_branch
        assert {"eq": {"is_shared": True}} in shared_branch

    def test_additive_without_uploads(self) -> None:
        """无上传件：仅共享库分支（单分支不再包 or）。"""
        expr = FilterBuilder.additive(
            school_id=uuid.uuid4(),
            course_id=uuid.uuid4(),
            source_type=SourceType.note,
            upload_ids=[],
        )

        assert "and" in expr
        assert {"eq": {"is_shared": True}} in expr["and"]

    def test_additive_has_no_user_scope_key(self) -> None:
        """user_scope 由调用方统一 AND，additive 不内嵌（避免分支内重复）。"""
        expr = FilterBuilder.additive(
            school_id=uuid.uuid4(),
            course_id=uuid.uuid4(),
            source_type=SourceType.lecture,
            upload_ids=[uuid.uuid4()],
        )

        assert "user_scope" not in expr

    def test_user_scope(self) -> None:
        uid = uuid.uuid4()
        expr = FilterBuilder.user_scope(uid)
        assert expr == {
            "or": [
                {"eq": {"user_id": str(uid)}},
                {"eq": {"is_shared": True}},
            ],
        }
