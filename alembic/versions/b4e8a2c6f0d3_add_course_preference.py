"""add course_preference table and paper_revision preference flags

Revision ID: b4e8a2c6f0d3
Revises: f2a9c6d4e8b1
Create Date: 2026-09-23 12:00:00.000000

修订链路新增「偏好沉淀」：用户勾选后由小 LLM 提炼把反馈泛化为课程级持久
偏好，按 (school_id, course_id) 作用域落库 course_preference 表（mem0 式
ADD/UPDATE/DELETE/NOOP 合并）。paper_revision 增加输入/结果两个布尔标记，
其中 save_preference 记录用户勾选，preference_saved 供修订历史回显。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.base import GUID

# revision identifiers, used by Alembic.
revision: str = 'b4e8a2c6f0d3'
down_revision: Union[str, None] = 'f2a9c6d4e8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'course_preference',
        sa.Column('id', GUID(), nullable=False),
        sa.Column('school_id', GUID(), nullable=False),
        sa.Column('course_id', GUID(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source_revision_id', GUID(), nullable=True),
        sa.Column('created_by', GUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['school_id'], ['school.id'], name=op.f('fk_course_preference_school_id_school'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['course_id'], ['course.id'], name=op.f('fk_course_preference_course_id_course'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_revision_id'], ['paper_revision.id'], name=op.f('fk_course_preference_source_revision_id_paper_revision'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_course_preference_created_by_app_user'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_course_preference')),
    )
    op.add_column(
        'paper_revision',
        sa.Column('save_preference', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column(
        'paper_revision',
        sa.Column('preference_saved', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )


def downgrade() -> None:
    op.drop_column('paper_revision', 'preference_saved')
    op.drop_column('paper_revision', 'save_preference')
    op.drop_table('course_preference')
