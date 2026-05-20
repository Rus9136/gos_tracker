"""Точка входа SQLAlchemy: engine, SessionLocal, init_db."""

from __future__ import annotations

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import DB_URL
from .models import Base

# `timeout=30` — SQLite по умолчанию ждёт блокировку 5с и роняется с
# `database is locked`. Поднимаем до 30с, чтобы uvicorn + CLI/скрипты могли
# работать одновременно (web-запросы короткие, скрипты пишут пачками).
engine = create_engine(
    DB_URL, echo=False, future=True, connect_args={"timeout": 30}
)


# WAL-режим: одновременные read'ы не блокируют write'ы (и наоборот).
# Без него `daily`/`reanalyze` валят uvicorn-сессии с `database is locked`,
# как только uvicorn держит хоть один read во время чужого INSERT'а.
# journal_mode хранится в самом файле БД, поэтому достаточно выставить
# при первом подключении любого процесса.
@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL = безопасно и быстро
    cur.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Узкий список ALTER-ов для существующих БД (новые таблицы добираются через
# create_all). Каждая запись: (таблица, колонка, DDL). idempotent.
_EXTRA_COLUMNS: list[tuple[str, str, str]] = [
    ("scrape_runs", "llm_analyzed", "INTEGER NOT NULL DEFAULT 0"),
    ("scrape_runs", "note", "TEXT"),
    ("lots", "is_starred", "BOOLEAN NOT NULL DEFAULT 0"),
]


def _ensure_columns() -> None:
    insp = inspect(engine)
    if not insp.has_table("scrape_runs"):
        return
    with engine.begin() as conn:
        for table, column, ddl in _EXTRA_COLUMNS:
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if column in existing:
                continue
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))


def init_db() -> None:
    """Создаёт таблицы (idempotent) и добавляет недостающие колонки."""
    Base.metadata.create_all(engine)
    _ensure_columns()


def get_session() -> Session:
    return SessionLocal()
