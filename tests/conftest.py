"""Общий conftest для pytest.

Изолируем тесты от боевого `data/goszakup.sqlite`: подменяем DB_URL на
tmp-файл ДО любого импорта `goszakup.*`. Это безопасно — `setdefault`
сохраняет явные переменные окружения (например, для интеграционных
прогонов на Postgres).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="goszakup-test-"))
os.environ.setdefault("GZ_DATABASE_URL", f"sqlite:///{_TEST_DB_DIR}/test.sqlite")
os.environ.setdefault("GZ_NO_AUTH", "1")
os.environ.setdefault("GZ_TEST_MODE", "1")


def pytest_configure(config):  # noqa: ARG001
    # Если кто-то нечаянно прогонит интеграционный тест локально с боевой
    # БД — лучше упасть на старте. Явные тесты сами перетирают переменную.
    os.environ.setdefault("GZ_TEST_MODE", "1")


import pytest


@pytest.fixture()
def db_session():
    """Чистая SQLite-сессия с применённой схемой. Между тестами очищает
    таблицы, чтобы не зависеть от порядка выполнения.

    Использовать вместо ручного importlib.reload(engine_mod) — он ломает
    глобальный state engine и валит соседние тесты.
    """
    from sqlalchemy import text as _text

    from goszakup.db.engine import SessionLocal, init_db

    init_db()
    s = SessionLocal()
    # Лоты могут ссылаться на анализ через cascade, поэтому порядок важен:
    # сначала зависимые таблицы.
    for tbl in (
        "user_lot_matches",
        "user_queries",
        # users чистим тоже: после фазы C watchlist — функция таблицы users,
        # и утёкшая строка молча меняет глобальный предикат should_analyze
        # (порядко-зависимая флакота в соседних тестах).
        "users",
        "lot_analyses",
        "contracts",
        "lot_status_history",
        "documents",
        "lot_bids",
        "lots",
        "announcements",
        "organizations",
        "scrape_runs",
        "presets",
    ):
        try:
            s.execute(_text(f'DELETE FROM "{tbl}"'))
        except Exception:
            s.rollback()
    s.commit()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
