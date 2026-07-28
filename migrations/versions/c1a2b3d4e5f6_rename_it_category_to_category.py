"""rename it_category -> category (фаза A SaaS-пивота)

Написана руками: autogenerate предложил бы drop+add с потерей данных.

Rename: lots.it_category -> category, users.it_categories -> categories,
presets.it_categories -> categories. Данные: 4 старых русских IT-подкатегории
схлопываются в слаг 'it' (classify/verticals.py); scope пользователей —
непустые списки -> ['it'], НЕ-админам пустой scope тоже -> ['it'] (раньше
«пусто = без ограничения» было безвредно — в БД жили только IT-лоты; после
фазы A пустой scope показал бы весь рынок). Presets: непустые -> ['it'],
seed-NULL остаются NULL (= «хранить всё» — желаемое покрытие HTML-фолбэка).

Downgrade lossy: подкатегории ('Оборудование', 'Услуги ИТ', ...) не
восстановимы — остаётся 'it'. Truthy-гейты старого кода это переживают,
но UI-селекты со старыми значениями не совпадут.

Revision ID: c1a2b3d4e5f6
Revises: 9fb8d8237c10
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '9fb8d8237c10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    # RENAME COLUMN нативен и на PG, и на SQLite >=3.25 — batch-режим
    # (пересоздание таблицы) тут не нужен.
    op.alter_column("lots", "it_category", new_column_name="category")
    op.alter_column("users", "it_categories", new_column_name="categories")
    op.alter_column("presets", "it_categories", new_column_name="categories")
    if dialect == "postgresql":
        op.execute("ALTER INDEX ix_lots_it_category RENAME TO ix_lots_category")
    else:
        op.drop_index("ix_lots_it_category", table_name="lots")
        op.create_index("ix_lots_category", "lots", ["category"])

    # В lots только 4 старых IT-значения — все схлопываются в 'it'.
    op.execute("UPDATE lots SET category = 'it' WHERE category IS NOT NULL")
    if dialect == "postgresql":
        op.execute(
            "UPDATE users SET categories = '[\"it\"]'::jsonb "
            "WHERE NOT is_admin "
            "OR (categories IS NOT NULL AND jsonb_array_length(categories) > 0)"
        )
        op.execute(
            "UPDATE presets SET categories = '[\"it\"]'::jsonb "
            "WHERE categories IS NOT NULL AND jsonb_array_length(categories) > 0"
        )
    else:
        op.execute(
            "UPDATE users SET categories = '[\"it\"]' "
            "WHERE is_admin = 0 "
            "OR (categories IS NOT NULL AND categories NOT IN ('[]', 'null'))"
        )
        op.execute(
            "UPDATE presets SET categories = '[\"it\"]' "
            "WHERE categories IS NOT NULL AND categories NOT IN ('[]', 'null')"
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.alter_column("lots", "category", new_column_name="it_category")
    op.alter_column("users", "categories", new_column_name="it_categories")
    op.alter_column("presets", "categories", new_column_name="it_categories")
    if dialect == "postgresql":
        op.execute("ALTER INDEX ix_lots_category RENAME TO ix_lots_it_category")
    else:
        op.drop_index("ix_lots_category", table_name="lots")
        op.create_index("ix_lots_it_category", "lots", ["it_category"])
