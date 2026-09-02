"""organizations.industry — отрасль организации (слаг industries.INDUSTRIES)

Revision ID: dee190c08423
Revises: bb09e3705f64
Create Date: 2026-09-02 10:00:00

Написана вручную: автоген снова предлагал снести trgm-индексы, которых нет
в моделях (см. bb09e3705f64).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dee190c08423'
down_revision: Union[str, Sequence[str], None] = 'bb09e3705f64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('industry', sa.String(length=20), nullable=True))
        batch_op.create_index(batch_op.f('ix_organizations_industry'), ['industry'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('organizations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organizations_industry'))
        batch_op.drop_column('industry')
