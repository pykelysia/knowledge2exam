"""chunk 表元数据改造：删 page，增 heading_path/char 偏移/embedding_text

Revision ID: d7c1e4f9a2b8
Revises: e5a7b9c1d2f3
Create Date: 2026-10-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd7c1e4f9a2b8'
down_revision: Union[str, None] = 'e5a7b9c1d2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 先加列回填，再删 page；embedding_text = text 与存量向量语义一致
    # （旧向量本就以 text 为输入），标题语义需重新上传解析后才有
    op.add_column('chunk', sa.Column('heading_path', sa.Text(), nullable=True))
    op.add_column('chunk', sa.Column('char_start', sa.Integer(), nullable=True))
    op.add_column('chunk', sa.Column('char_end', sa.Integer(), nullable=True))
    op.add_column('chunk', sa.Column('embedding_text', sa.Text(), nullable=True))
    op.execute('UPDATE chunk SET embedding_text = text')
    op.drop_column('chunk', 'page')


def downgrade() -> None:
    # page 列数据不可恢复：该列无消费方，重构时刻意不保留
    op.add_column('chunk', sa.Column('page', sa.Integer(), nullable=True))
    op.drop_column('chunk', 'embedding_text')
    op.drop_column('chunk', 'char_end')
    op.drop_column('chunk', 'char_start')
    op.drop_column('chunk', 'heading_path')
