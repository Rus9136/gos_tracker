"""Точка входа SQLAlchemy: engine, SessionLocal, init_db.

Поддерживаются два диалекта:
- SQLite (legacy, dev и пока ещё прод) — нужны WAL pragmas и `timeout=30`.
- Postgres (Phase 1+, docker-compose, будущий прод) — стандартный pool.

Разделение по `engine.dialect.name`, а не по строке URL — это надёжнее.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from ..config import DB_URL, ROOT
from .models import Base

log = logging.getLogger(__name__)

_is_sqlite = make_url(DB_URL).get_backend_name() == "sqlite"

if _is_sqlite:
    # `timeout=30` — SQLite по умолчанию ждёт блокировку 5с и роняется с
    # `database is locked`. Поднимаем до 30с, чтобы uvicorn + CLI/скрипты
    # могли работать одновременно (web-запросы короткие, скрипты пишут пачками).
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
else:
    # Postgres / другие: дефолтный pool, без специфичных pragmas.
    # pool_pre_ping=True — отлавливает «оборванные» соединения после
    # рестарта БД, особенно актуально в docker-compose окружении.
    engine = create_engine(DB_URL, echo=False, future=True, pool_pre_ping=True)


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


def _stamp_alembic_head() -> None:
    """Пометить только что созданную create_all-схему как Alembic head.

    Единственная истинная схема — миграции (`migrations/`). Но create_all может
    построить полную схему на пустой БД, минуя историю Alembic — тогда `alembic
    upgrade head` упадёт на «table already exists» или схема разойдётся молча.
    Поэтому свежесозданную БД сразу штампуем head: create_all строит ровно
    текущие модели = состояние head, а alembic_version делает это официальным.

    Config() без ini — чтобы env.py не звал fileConfig и не переопределял
    логирование приложения. Defensive: сбой штампа не должен ронять init_db.
    """
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        command.stamp(cfg, "head")
    except Exception as e:  # noqa: BLE001 — safety net, не критично для старта
        log.warning("alembic stamp head пропущен (%s)", e)


def init_db() -> None:
    """Создаёт таблицы (idempotent) и добавляет недостающие колонки.

    Канонический источник схемы — Alembic (`migrations/`). create_all оставлен
    как safety net для свежих/dev-БД; на пустой БД мы дополнительно штампуем
    Alembic head, чтобы create_all-схема и история миграций не расходились.
    """
    fresh = not inspect(engine).has_table("lots")
    Base.metadata.create_all(engine)
    _ensure_columns()
    if fresh:
        _stamp_alembic_head()


def get_session() -> Session:
    return SessionLocal()
