"""FilterBuilder 单元测试（白名单语义）。"""

from __future__ import annotations

import uuid

from app.core.enums import SourceType
from app.retrieval.filters import FilterBuilder


class TestFilterBuilder:
    def test_whitelist_uploads_and_source_types(self) -> None:
        """白名单 = 选中的上传件 × 可检索内容类型，AND 组合。"""
        u1, u2 = uuid.uuid4(), uuid.uuid4()

        expr = FilterBuilder.whitelist([u1, u2], [SourceType.book, SourceType.note])

        assert expr == {
            "and": [
                {"in": {"upload_id": [str(u1), str(u2)]}},
                {"in": {"source_type": ["book", "note"]}},
            ]
        }

    def test_whitelist_preserves_upload_order(self) -> None:
        ids = [uuid.uuid4() for _ in range(3)]
        expr = FilterBuilder.whitelist(ids, [SourceType.lecture])
        assert expr["and"][0]["in"]["upload_id"] == [str(u) for u in ids]

    def test_whitelist_has_no_legacy_keys(self) -> None:
        """白名单之外无跨用户分支：不含 school/course/is_shared/user_scope。"""
        expr = FilterBuilder.whitelist([uuid.uuid4()], [SourceType.book])
        flattened = repr(expr)
        for legacy in ("school_id", "course_id", "is_shared", "user_scope", "'or'"):
            assert legacy not in flattened
