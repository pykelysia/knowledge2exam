"""add paper_revision table (job-level revision session log)

Revision ID: f2a9c6d4e8b1
Revises: c3f8d90b1e47
Create Date: 2026-09-15 12:00:00.000000

试卷改为整卷直写 output/paper.md 后，修订以「划选反馈轮次」为单位续跑同一
agent。每轮留存划选锚点、反馈与前后试卷快照，构成 job 级会话记录与回溯依据。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.base import GUID

# revision identifiers, used by Alembic.
revision: str = 'f2a9c6d4e8b1'
down_revision: Union[str, None] = 'c3f8d90b1e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'paper_revision',
        sa.Column('id', GUID(), nullable=False),
        sa.Column('job_id', GUID(), nullable=False),
        sa.Column('round_no', sa.Integer(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='running'),
        sa.Column('selection', sa.postgresql.JSONB(), nullable=True),
        sa.Column('feedback', sa.Text(), nullable=False),
        sa.Column('snapshot_before', sa.Text(), nullable=False, server_default=''),
        sa.Column('snapshot_after', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['job.id'], name=op.f('fk_paper_revision_job_id_job'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_paper_revision')),
        sa.UniqueConstraint('job_id', 'round_no', name=op.f('uq_paper_revision_job_id_round_no')),
    )
    op.create_index('ix_paper_revision_job_id', 'paper_revision', ['job_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_paper_revision_job_id', table_name='paper_revision')
    op.drop_table('paper_revision')
