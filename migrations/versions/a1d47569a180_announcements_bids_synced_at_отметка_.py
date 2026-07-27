"""announcements.bids_synced_at — отметка опроса заявок

Revision ID: a1d47569a180
Revises: 2ac010466a64
Create Date: 2026-07-27 15:35:48.356466

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1d47569a180'
down_revision: Union[str, Sequence[str], None] = '2ac010466a64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('announcements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bids_synced_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_announcements_bids_synced_at'), ['bids_synced_at'], unique=False)



    # trgm-индексы в models.py не описаны (заведены вручную) — блок их
    # сноса из autogenerate удалён намеренно, как в cb03d00c8075.


def downgrade() -> None:
    """Downgrade schema."""


    with op.batch_alter_table('announcements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_announcements_bids_synced_at'))
        batch_op.drop_column('bids_synced_at')

