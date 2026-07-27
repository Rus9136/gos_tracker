"""announcements.application_start — начало приёма заявок

Revision ID: cb03d00c8075
Revises: 366463c4707d
Create Date: 2026-07-27 14:39:21.742631

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cb03d00c8075'
down_revision: Union[str, Sequence[str], None] = '366463c4707d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # trgm-индексы (ix_lots_*_trgm, ix_organizations_name_trgm) заведены в БД
    # вручную и в models.py не описаны — autogenerate предлагал их снести,
    # блок удалён намеренно.
    with op.batch_alter_table('announcements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('application_start', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_announcements_application_start'), ['application_start'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('announcements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_announcements_application_start'))
        batch_op.drop_column('application_start')
