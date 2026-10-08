"""file system overhaul

- course 全局化：去 school_id，name 全局唯一（学校与课程为互相独立的标签）
- app_user 增学校归属 school_id（本校可见的观看资格）
- upload 吸收 resource：+visibility/school_id/course_id/parsed_key/char_count/
  parse_warnings/deleted_at，-shareable；软删除
- chunk 直接挂 upload：去 resource_id/user_id/school_id/course_id/is_shared，
  唯一约束改 (upload_id, chunk_index)
- 新增 file_import 个人库引用表；删除 resource 表

按决策清空存量业务数据（保留 app_user / refresh_token）；
升级后需手动执行 `python -m app.core.seed` 重灌目录数据。

Revision ID: e5a7b9c1d2f3
Revises: b4e8a2c6f0d3
Create Date: 2026-10-08 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.base import GUID

# revision identifiers, used by Alembic.
revision: str = "e5a7b9c1d2f3"
down_revision: Union[str, None] = "b4e8a2c6f0d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VISIBILITY_ENUM = postgresql.ENUM(
    "private", "school", "public", name="visibility", create_type=False
)


def upgrade() -> None:
    conn = op.get_bind()

    # 1. 清空存量业务数据（决策：清空重建，保留用户账号）
    conn.execute(
        sa.text(
            "TRUNCATE TABLE resource, chunk, job_upload, job_stage, plan_item, "
            "paper_revision, course_preference, job, upload, course, school "
            "RESTART IDENTITY CASCADE"
        )
    )

    # 2. course 全局化
    op.drop_constraint(op.f("fk_course_school_id_school"), "course", type_="foreignkey")
    op.drop_constraint(op.f("uq_course_school_id"), "course", type_="unique")
    op.drop_column("course", "school_id")
    op.create_unique_constraint(op.f("uq_course_name"), "course", ["name"])

    # 3. app_user 学校归属
    op.add_column("app_user", sa.Column("school_id", GUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_app_user_school_id_school"), "app_user", "school", ["school_id"], ["id"]
    )

    # 4. upload 重构
    # 旧 shareable 约束与列
    op.drop_constraint(
        op.f("ck_upload_manual_not_shareable"), "upload", type_="check"
    )
    op.drop_column("upload", "shareable")

    visibility_enum = postgresql.ENUM(
        "private", "school", "public", name="visibility", create_type=True
    )
    visibility_enum.create(conn)
    op.add_column(
        "upload",
        sa.Column("visibility", VISIBILITY_ENUM, nullable=False, server_default="private"),
    )
    op.add_column("upload", sa.Column("school_id", GUID(), nullable=True))
    op.add_column("upload", sa.Column("course_id", GUID(), nullable=True))
    op.add_column("upload", sa.Column("parsed_key", sa.Text(), nullable=True))
    op.add_column("upload", sa.Column("char_count", sa.Integer(), nullable=True))
    op.add_column(
        "upload",
        sa.Column(
            "parse_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "upload", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_upload_school_id_school"), "upload", "school", ["school_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_upload_course_id_course"), "upload", "course", ["course_id"], ["id"]
    )
    op.create_check_constraint(
        op.f("ck_upload_manual_not_shareable"),
        "upload",
        "source_type NOT IN ('manual_text', 'extra_requirement') "
        "OR visibility = 'private'",
    )
    op.create_check_constraint(
        op.f("ck_upload_school_visibility_needs_school"),
        "upload",
        "visibility != 'school' OR school_id IS NOT NULL",
    )
    op.create_index(
        "idx_upload_pool",
        "upload",
        ["visibility", "school_id", "course_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # 5. chunk 直接挂 upload
    op.drop_index("idx_chunk_payload", table_name="chunk")
    op.drop_constraint(op.f("uq_chunk_resource_id"), "chunk", type_="unique")
    op.drop_constraint(
        op.f("fk_chunk_resource_id_resource"), "chunk", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_chunk_user_id_app_user"), "chunk", type_="foreignkey")
    op.drop_constraint(op.f("fk_chunk_school_id_school"), "chunk", type_="foreignkey")
    op.drop_constraint(op.f("fk_chunk_course_id_course"), "chunk", type_="foreignkey")
    op.drop_column("chunk", "resource_id")
    op.drop_column("chunk", "user_id")
    op.drop_column("chunk", "school_id")
    op.drop_column("chunk", "course_id")
    op.drop_column("chunk", "is_shared")
    op.create_unique_constraint(
        op.f("uq_chunk_upload_id"), "chunk", ["upload_id", "chunk_index"]
    )
    op.create_index("idx_chunk_upload", "chunk", ["upload_id"], unique=False)

    # 6. 删除 resource 表
    op.drop_index("idx_resource_shared_scope", table_name="resource")
    op.drop_table("resource")

    # 7. file_import 个人库引用表
    op.create_table(
        "file_import",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("upload_id", GUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_file_import_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["upload.id"],
            name=op.f("fk_file_import_upload_id_upload"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_import")),
        sa.UniqueConstraint(
            "user_id", "upload_id", name=op.f("uq_file_import_user_id")
        ),
    )


def downgrade() -> None:
    """逆向 DDL。与 upgrade 对称地清空业务数据（本迁移族数据视为可丢弃，
    用户账号保留）；重加的 NOT NULL 列依赖清空后的空表。"""

    conn = op.get_bind()

    # 0. 清空业务数据（与 upgrade 对称；resource 表此时尚未重建）
    conn.execute(
        sa.text(
            "TRUNCATE TABLE file_import, chunk, job_upload, job_stage, plan_item, "
            "paper_revision, course_preference, job, upload, course, school "
            "RESTART IDENTITY CASCADE"
        )
    )

    # 7. file_import
    op.drop_table("file_import")

    # 6. resource 表（按 initial schema 重建）
    op.create_table(
        "resource",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("upload_id", GUID(), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                "book",
                "lecture",
                "note",
                "keypoint_list",
                "past_paper",
                "manual_text",
                "extra_requirement",
                name="source_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("school_id", GUID(), nullable=True),
        sa.Column("course_id", GUID(), nullable=True),
        sa.Column("parsed_key", sa.Text(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column(
            "is_shared", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "is_shared = false OR (school_id IS NOT NULL AND course_id IS NOT NULL)",
            name=op.f("ck_resource_shared_needs_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["course_id"], ["course.id"], name=op.f("fk_resource_course_id_course")
        ),
        sa.ForeignKeyConstraint(
            ["school_id"], ["school.id"], name=op.f("fk_resource_school_id_school")
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["upload.id"],
            name=op.f("fk_resource_upload_id_upload"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource")),
    )
    op.create_index(
        "idx_resource_shared_scope",
        "resource",
        ["school_id", "course_id", "source_type"],
        unique=False,
        postgresql_where=sa.text("is_shared = true"),
    )

    # 5. chunk 恢复旧结构
    op.drop_index("idx_chunk_upload", table_name="chunk")
    op.drop_constraint(op.f("uq_chunk_upload_id"), "chunk", type_="unique")
    op.add_column("chunk", sa.Column("resource_id", GUID(), nullable=False))
    op.add_column("chunk", sa.Column("user_id", GUID(), nullable=False))
    op.add_column("chunk", sa.Column("school_id", GUID(), nullable=True))
    op.add_column("chunk", sa.Column("course_id", GUID(), nullable=True))
    op.add_column(
        "chunk",
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_foreign_key(
        op.f("fk_chunk_resource_id_resource"),
        "chunk",
        "resource",
        ["resource_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_chunk_user_id_app_user"), "chunk", "app_user", ["user_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_chunk_school_id_school"), "chunk", "school", ["school_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_chunk_course_id_course"), "chunk", "course", ["course_id"], ["id"]
    )
    op.create_unique_constraint(
        op.f("uq_chunk_resource_id"), "chunk", ["resource_id", "chunk_index"]
    )
    op.create_index(
        "idx_chunk_payload",
        "chunk",
        ["school_id", "course_id", "source_type", "is_shared"],
        unique=False,
    )

    # 4. upload 恢复 shareable
    op.drop_index("idx_upload_pool", table_name="upload")
    op.drop_constraint(
        op.f("ck_upload_school_visibility_needs_school"), "upload", type_="check"
    )
    op.drop_constraint(
        op.f("ck_upload_manual_not_shareable"), "upload", type_="check"
    )
    op.drop_constraint(op.f("fk_upload_course_id_course"), "upload", type_="foreignkey")
    op.drop_constraint(op.f("fk_upload_school_id_school"), "upload", type_="foreignkey")
    op.drop_column("upload", "deleted_at")
    op.drop_column("upload", "parse_warnings")
    op.drop_column("upload", "char_count")
    op.drop_column("upload", "parsed_key")
    op.drop_column("upload", "course_id")
    op.drop_column("upload", "school_id")
    op.drop_column("upload", "visibility")
    postgresql.ENUM(name="visibility").drop(conn, checkfirst=True)
    op.add_column(
        "upload",
        sa.Column("shareable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_check_constraint(
        op.f("ck_upload_manual_not_shareable"),
        "upload",
        "source_type NOT IN ('manual_text', 'extra_requirement') OR shareable = false",
    )

    # 3. app_user 去 school_id
    op.drop_constraint(
        op.f("fk_app_user_school_id_school"), "app_user", type_="foreignkey"
    )
    op.drop_column("app_user", "school_id")

    # 2. course 恢复从属 school
    op.drop_constraint(op.f("uq_course_name"), "course", type_="unique")
    op.add_column("course", sa.Column("school_id", GUID(), nullable=False))
    op.create_foreign_key(
        op.f("fk_course_school_id_school"),
        "course",
        "school",
        ["school_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        op.f("uq_course_school_id"), "course", ["school_id", "name"]
    )
