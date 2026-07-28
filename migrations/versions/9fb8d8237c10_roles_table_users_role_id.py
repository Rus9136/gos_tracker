"""roles table + users.role_id

Роль UI — список ключей вкладок (web/pages.PAGES), видимых не-админу.
NULL role_id = все пользовательские вкладки.

Autogenerate также предлагал DROP trgm-индексов (ix_lots_*_trgm,
ix_organizations_name_trgm) — они созданы вне моделей и из миграции убраны.

Revision ID: 9fb8d8237c10
Revises: 48118657f78b
Create Date: 2026-07-28 05:04:22.219425

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9fb8d8237c10'
down_revision: Union[str, Sequence[str], None] = '48118657f78b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('roles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('pages', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_users_role_id_roles', 'roles', ['role_id'], ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_role_id_roles', type_='foreignkey')
        batch_op.drop_column('role_id')

    op.drop_table('roles')
