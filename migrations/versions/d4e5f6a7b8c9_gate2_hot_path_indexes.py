"""gate2: составные индексы под горячие запросы

Revision ID: d4e5f6a7b8c9
Revises: c3a7e1f09b21
Create Date: 2026-07-15

Готовим схему к 700-800K лотов/год: составные индексы под /actual, /past,
/matched, /runs. Одноколоночные индексы уже есть; здесь — композитные, которые
закрывают фильтр+сортировку одним проходом.

При истинном объёме на Postgres предпочтительно CREATE INDEX CONCURRENTLY (не
блокирует запись), но это требует autocommit-block и оставляет INVALID индекс
при сбое. На текущем объёме применяем обычным create_index в низкий трафик.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3a7e1f09b21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_lots_actual_first_seen", "lots", ["is_actual", "first_seen"])
    op.create_index("ix_lots_last_synced", "lots", ["last_synced"])
    op.create_index(
        "ix_match_query_matched_score",
        "user_lot_matches",
        ["user_query_id", "matched", "score"],
    )


def downgrade() -> None:
    op.drop_index("ix_match_query_matched_score", table_name="user_lot_matches")
    op.drop_index("ix_lots_last_synced", table_name="lots")
    op.drop_index("ix_lots_actual_first_seen", table_name="lots")
