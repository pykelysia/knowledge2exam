"""鉴权 / 上传 schema 校验测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.auth import LoginRequest, RegisterRequest
from app.schemas.upload import TextUploadCreate


class TestPasswordByteLimit:
    def test_register_accepts_72_byte_password(self) -> None:
        # 24 个中文 = 72 字节，恰好在 bcrypt 上限内
        payload = RegisterRequest(email="a@b.com", username="u", password="测" * 24)
        assert payload.password == "测" * 24

    def test_register_rejects_over_72_bytes(self) -> None:
        # 25 个中文 = 75 字节，超过 bcrypt 限制
        with pytest.raises(ValidationError, match="72"):
            RegisterRequest(email="a@b.com", username="u", password="测" * 25)

    def test_login_rejects_over_72_bytes(self) -> None:
        with pytest.raises(ValidationError, match="72"):
            LoginRequest(email="a@b.com", password="x" * 73)

    def test_login_requires_email_or_username(self) -> None:
        with pytest.raises(ValidationError):
            LoginRequest(password="password123")

    def test_register_min_length_still_enforced(self) -> None:
        with pytest.raises(ValidationError):
            RegisterRequest(email="a@b.com", username="u", password="short")


class TestTextUploadCreate:
    def test_rejects_file_only_source_type(self) -> None:
        with pytest.raises(ValidationError, match="source_type"):
            TextUploadCreate(source_type="book", raw_text="文本")

    def test_rejects_empty_text(self) -> None:
        with pytest.raises(ValidationError):
            TextUploadCreate(source_type="manual_text", raw_text="")

    def test_rejects_oversized_text(self) -> None:
        with pytest.raises(ValidationError):
            TextUploadCreate(source_type="manual_text", raw_text="a" * 200_001)

    def test_accepts_valid_text(self) -> None:
        payload = TextUploadCreate(source_type="extra_requirement", raw_text="侧重计算")
        assert payload.source_type == "extra_requirement"
