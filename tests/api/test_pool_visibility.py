"""公开池可见性判定单元测试。"""

from __future__ import annotations

import uuid

from app.api.pool import _pool_visible
from app.core.enums import SourceType, Visibility
from app.models.upload import Upload


def _user(school_id: uuid.UUID | None = None):
    return type("U", (), {"id": uuid.uuid4(), "school_id": school_id})()


def _upload(visibility: Visibility, school_id: uuid.UUID | None = None) -> Upload:
    return Upload(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        source_type=SourceType.note,
        visibility=visibility,
        school_id=school_id,
    )


class TestPoolVisible:
    def test_public_visible_to_everyone(self) -> None:
        upload = _upload(Visibility.public)
        assert _pool_visible(_user(school_id=None), upload) is True
        assert _pool_visible(_user(school_id=uuid.uuid4()), upload) is True

    def test_school_visible_only_to_same_school(self) -> None:
        school = uuid.uuid4()
        upload = _upload(Visibility.school, school_id=school)

        assert _pool_visible(_user(school_id=school), upload) is True
        assert _pool_visible(_user(school_id=uuid.uuid4()), upload) is False
        assert _pool_visible(_user(school_id=None), upload) is False

    def test_school_visibility_without_school_tag_never_visible(self) -> None:
        # CHECK 约束已保证 school 可见必有 school_id，防御式兜底
        upload = _upload(Visibility.school, school_id=None)
        assert _pool_visible(_user(school_id=uuid.uuid4()), upload) is False

    def test_private_never_in_pool(self) -> None:
        upload = _upload(Visibility.private, school_id=uuid.uuid4())
        assert _pool_visible(_user(school_id=upload.school_id), upload) is False
