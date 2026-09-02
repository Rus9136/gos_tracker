"""organizations.oked + oked_synced_at — ОКЭД из реестра участников OWS

Revision ID: 71d5b69e5ff5
Revises: dee190c08423
Create Date: 2026-09-02 12:00:00

Написана вручную (автоген предлагает снести trgm-индексы, см. bb09e3705f64).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71d5b69e5ff5'
down_revision: Union[str, Sequence[str], None] = 'dee190c08423'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('oked', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('oked_synced_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_organizations_oked_synced_at'), ['oked_synced_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organizations_oked_synced_at'))
        batch_op.drop_column('oked_synced_at')
        batch_op.drop_column('oked')
