"""lot winner_bin/winner_name из вкладки победителей

Revision ID: 635bb7f06534
Revises: f6a7b8c9d0e1
Create Date: 2026-07-16 14:54:34.356836

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '635bb7f06534'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# trgm-индексы (ix_lots_enstru_trgm и т.п.) созданы на проде вручную и в
# моделях не описаны — autogenerate предлагал их снести, эти строки удалены.


def upgrade() -> None:
    with op.batch_alter_table('lots', schema=None) as batch_op:
        batch_op.add_column(sa.Column('winner_bin', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('winner_name', sa.String(length=500), nullable=True))
        batch_op.create_index(batch_op.f('ix_lots_winner_bin'), ['winner_bin'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('lots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lots_winner_bin'))
        batch_op.drop_column('winner_name')
        batch_op.drop_column('winner_bin')
