"""个人库引用表 `file_import`。

公开池「保存到个人库」的引用级授权记录（不复制数据）：
- 创建任务时可引用 = 本人上传 OR 存在 file_import 行
- 原件软删除/转私不影响已保存引用（授权落库即持久），
  仅原件删除后个人库条目标记「文件已失效」
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, uuid_pk


class FileImport(Base, TimestampMixin):
    __tablename__ = "file_import"
    __table_args__ = (UniqueConstraint("user_id", "upload_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    upload_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload.id", ondelete="CASCADE"), nullable=False
    )
