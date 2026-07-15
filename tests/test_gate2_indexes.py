"""Гейт 2: составные индексы под горячие запросы существуют в схеме.

Регресс: чтобы случайно не удалили индекс, на котором держатся /actual, /past,
/matched, /runs при 800K лотов.
"""

from __future__ import annotations

from sqlalchemy import inspect

from goszakup.db.engine import SessionLocal, engine, init_db


def _index_names(table: str) -> set[str]:
    init_db()
    SessionLocal().close()
    return {ix["name"] for ix in inspect(engine).get_indexes(table)}


def test_lots_composite_indexes_exist():
    names = _index_names("lots")
    assert "ix_lots_actual_first_seen" in names
    assert "ix_lots_last_synced" in names


def test_matches_composite_index_exists():
    assert "ix_match_query_matched_score" in _index_names("user_lot_matches")
