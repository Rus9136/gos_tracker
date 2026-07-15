"""gate2: pg_trgm GIN-индексы под поиск ILIKE '%q%'

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-15

Поиск в /actual/past (_lots_query) делает leading-wildcard ILIKE по lots.name
(Text, без индекса вообще), lots.enstru и organizations.name. Btree такой ILIKE
не использует — full scan на 800K строк. GIN + gin_trgm_ops это чинит.

Postgres-only: pg_trgm нет в SQLite, поэтому в models.py индекс НЕ объявлен
(create_all на dev-SQLite его бы не понял), а живёт только здесь. Требует, чтобы
роль могла CREATE EXTENSION pg_trgm (на системном PG обычно да; иначе DBA создаёт
расширение заранее — CREATE EXTENSION IF NOT EXISTS идемпотентен).
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite (dev/CI) — trgm недоступен, поиск на малом объёме и так ок
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lots_name_trgm "
        "ON lots USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_lots_enstru_trgm "
        "ON lots USING gin (enstru gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_organizations_name_trgm "
        "ON organizations USING gin (name gin_trgm_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_organizations_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_lots_enstru_trgm")
    op.execute("DROP INDEX IF EXISTS ix_lots_name_trgm")
