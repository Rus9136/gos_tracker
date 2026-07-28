"""organizations: website + contacts_synced_at

Revision ID: 48118657f78b
Revises: a1d47569a180
Create Date: 2026-07-28 04:04:14.338294

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '48118657f78b'
down_revision: Union[str, Sequence[str], None] = 'a1d47569a180'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Autogenerate также предлагал drop триграммных GIN-индексов
# (ix_lots_*_trgm, ix_organizations_name_trgm) — они созданы вручную
# вне моделей и нужны поиску; из миграции убраны.


def upgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('website', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('contacts_synced_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_organizations_contacts_synced_at'), ['contacts_synced_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organizations_contacts_synced_at'))
        batch_op.drop_column('contacts_synced_at')
        batch_op.drop_column('website')
