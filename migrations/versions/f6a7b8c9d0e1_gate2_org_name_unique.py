"""gate2: дедуп безбиновых организаций + частичный уникальный индекс по имени

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-15

Организации из листинга приходят без БИН (goszakup не показывает БИН заказчика),
их идентичность — имя. get-then-insert без констрейнта позволял параллельным
прогонам (daily vs /scan) создать дубль. Ставим частичный уникальный индекс
name WHERE bin IS NULL. Перед этим дедупим уже накопившиеся дубли: FK
(lots.customer_id, announcements.organizer_id, contracts.supplier_id) репоинтим
на канонический min(id) по имени, лишние строки удаляем. Дедуп — только Postgres
(dev/CI SQLite создаётся с нуля, дублей нет).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CANON = (
    "SELECT id, min(id) OVER (PARTITION BY name) AS keep "
    "FROM organizations WHERE bin IS NULL"
)


def _dedup_pg() -> None:
    for table, col in (
        ("lots", "customer_id"),
        ("announcements", "organizer_id"),
        ("contracts", "supplier_id"),
    ):
        op.execute(
            f"WITH canon AS ({_CANON}) "
            f"UPDATE {table} t SET {col} = c.keep FROM canon c "
            f"WHERE t.{col} = c.id AND c.id <> c.keep"
        )
    op.execute(
        f"WITH canon AS ({_CANON}) "
        "DELETE FROM organizations o USING canon c "
        "WHERE o.id = c.id AND c.id <> c.keep"
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _dedup_pg()
    op.create_index(
        "uq_org_name_no_bin",
        "organizations",
        ["name"],
        unique=True,
        postgresql_where=sa.text("bin IS NULL"),
        sqlite_where=sa.text("bin IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_org_name_no_bin", table_name="organizations")
