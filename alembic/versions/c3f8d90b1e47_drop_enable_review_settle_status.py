"""drop job.enable_review and settle stale non-terminal statuses

Revision ID: c3f8d90b1e47
Revises: a1b2c3d4e5f6
Create Date: 2026-09-11 12:00:00.000000

背景（docs/workflow.jpg）：审查阶段并入 agent 工作内部，reviewing/planning
不再是合法状态。pipeline 是进程内 asyncio 任务，服务重启即死，历史遗留的
非终态 job 不可能再推进，统一定格为 failed，否则 JobStatus(...) 查找抛
ValueError 导致 API 500。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3f8d90b1e47'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 迁移执行于新代码部署前，此时库里尚无本次改动产生的运行态；
# 旧代码的全部非终态均在此列。
_NON_TERMINAL_STATUSES = (
    'pending',
    'preprocessing',
    'planning',
    'generating',
    'reviewing',
    'rendering',
)


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE job SET status = 'failed', "
            "error_code = COALESCE(error_code, 'PIPELINE_FAILED'), "
            "finished_at = COALESCE(finished_at, now()) "
            "WHERE status IN :statuses"
        ).bindparams(sa.bindparam("statuses", value=list(_NON_TERMINAL_STATUSES)))
    )
    op.drop_column('job', 'enable_review')


def downgrade() -> None:
    op.add_column(
        'job',
        sa.Column('enable_review', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    # 历史状态（planning/reviewing）无法从 'failed' 还原，不做回写
