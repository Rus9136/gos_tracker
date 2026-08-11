"""lots.bids_count — число участников по лоту

Revision ID: bb09e3705f64
Revises: c9de022db666
Create Date: 2026-08-07 08:21:52.946763

Автоген предлагал заодно снести trgm-индексы (ix_lots_name_trgm и др.) —
они заведены отдельной миграцией сырым SQL и в моделях не описаны, так что
для сравнения схем их «нет». Удалены из этой ревизии вручную.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb09e3705f64'
down_revision: Union[str, Sequence[str], None] = 'c9de022db666'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('lots', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bids_count', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_lots_bids_count'), ['bids_count'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('lots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lots_bids_count'))
        batch_op.drop_column('bids_count')
