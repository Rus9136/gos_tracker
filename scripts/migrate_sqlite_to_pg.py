"""Перелить данные из SQLite в Postgres.

Идём по таблицам в порядке зависимостей (FK):
  presets / organizations → announcements → lots → lot_status_history
  → documents → contracts → lot_analyses → scrape_runs

Идемпотентность — `INSERT ... ON CONFLICT (id) DO NOTHING` на стороне Postgres.
Поэтому скрипт можно перезапускать; повторно строки не вставит.

Использование (запускать с *хоста*, не из контейнера):

  # Источник — БД по умолчанию (data/goszakup.sqlite).
  # Назначение — задаётся отдельно, не через GZ_DATABASE_URL.
  GZ_DEST_DATABASE_URL='postgresql+psycopg://goszakup:goszakup_dev@localhost:5433/goszakup' \\
      .venv/bin/python -m scripts.migrate_sqlite_to_pg

  # Можно перебить и источник:
  GZ_SRC_DATABASE_URL='sqlite:////path/to/old.sqlite' \\
  GZ_DEST_DATABASE_URL='...' \\
      .venv/bin/python -m scripts.migrate_sqlite_to_pg

Назначение **должно** быть на актуальной схеме (`alembic upgrade head`)
до запуска скрипта.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from goszakup.config import DB_URL as DEFAULT_SRC_URL  # noqa: E402
from goszakup.db.models import (  # noqa: E402
    Announcement,
    Contract,
    Document,
    Lot,
    LotAnalysis,
    LotStatusHistory,
    Organization,
    Preset,
    ScrapeRun,
)

log = logging.getLogger("migrate")

# Порядок миграции — критичен. Сначала «независимые» таблицы, потом те,
# что ссылаются на них через FK.
TABLES_IN_ORDER = [
    Organization,
    Preset,
    Announcement,
    Lot,
    LotStatusHistory,
    Document,
    Contract,
    LotAnalysis,
    ScrapeRun,
]

BATCH_SIZE = 1000


def _row_to_dict(model_cls, obj) -> dict:
    return {col.name: getattr(obj, col.name) for col in model_cls.__table__.columns}


def migrate_table(src_session: Session, dst_session: Session, model_cls) -> tuple[int, int]:
    """Возвращает (прочитано из src, новых строк в dst)."""
    table = model_cls.__table__
    # psycopg на bulk INSERT ... ON CONFLICT DO NOTHING возвращает rowcount=-1
    # (driver не знает, сколько строк реально вставилось). Считаем дельтой
    # COUNT(*) до и после — точно, не зависит от драйвера.
    before = dst_session.scalar(select(func.count()).select_from(table)) or 0

    total_read = 0
    batch: list[dict] = []
    for obj in src_session.scalars(select(model_cls)).yield_per(BATCH_SIZE):
        batch.append(_row_to_dict(model_cls, obj))
        total_read += 1
        if len(batch) >= BATCH_SIZE:
            _flush_batch(dst_session, table, batch)
            batch.clear()
    if batch:
        _flush_batch(dst_session, table, batch)
    dst_session.commit()

    after = dst_session.scalar(select(func.count()).select_from(table)) or 0
    return total_read, after - before


def _flush_batch(dst_session: Session, table, batch: list[dict]) -> None:
    if not batch:
        return
    stmt = pg_insert(table).values(batch).on_conflict_do_nothing(
        index_elements=[table.primary_key.columns.values()[0]]
    )
    dst_session.execute(stmt)


def main() -> int:
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    src_url = os.environ.get("GZ_SRC_DATABASE_URL", DEFAULT_SRC_URL)
    dst_url = os.environ.get("GZ_DEST_DATABASE_URL")
    if not dst_url:
        log.error("GZ_DEST_DATABASE_URL не задан")
        return 1
    if not dst_url.startswith("postgresql"):
        log.error("назначение должно быть Postgres (INSERT ... ON CONFLICT — PG-only)")
        return 1

    log.info("src: %s", src_url)
    log.info("dst: %s", dst_url)

    src_engine = create_engine(src_url, future=True)
    dst_engine = create_engine(dst_url, future=True)

    # Sanity: убедимся, что назначение в schema актуальной версии (есть наши таблицы).
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(dst_engine)
    missing = [m.__tablename__ for m in TABLES_IN_ORDER if not insp.has_table(m.__tablename__)]
    if missing:
        log.error(
            "в назначении нет таблиц: %s — сначала `alembic upgrade head`", missing
        )
        return 1

    grand_total = 0
    with Session(src_engine) as src, Session(dst_engine) as dst:
        # Pre-step: в SQLite на проде есть stub-лоты — `Lot.announcement_id`
        # указывает на объявление, которое ещё не сохранено в `announcements`
        # (CLAUDE.md, секция про stub-лоты + кнопка «Загрузить документы»).
        # В Postgres FK строгий, поэтому до миграции Lot заводим stub-записи
        # для всех таких orphan-anno_id. Когда позже подтянутся реальные
        # детали — те поля просто проапдейтятся.
        _stub_announcement_orphans(src, dst)

        for model_cls in TABLES_IN_ORDER:
            read, inserted = migrate_table(src, dst, model_cls)
            log.info(
                "%s: прочитано %d, вставлено %d (skipped %d — уже были)",
                model_cls.__tablename__, read, inserted, read - inserted,
            )
            grand_total += inserted

    log.info("ИТОГО вставлено: %d строк", grand_total)
    # Не трогаем sequences — у нас Integer PK заполнены явными значениями.
    # Если Postgres-сторона позже начнёт сама генерить id (autoincrement),
    # нужно будет вызвать `SELECT setval(...)` для каждой таблицы. Сейчас
    # все наши вставки приходят с готовыми id из источника.
    _bump_sequences(dst_engine)
    return 0


def _stub_announcement_orphans(src: Session, dst: Session) -> None:
    """Заводит stub-Announcement для каждого announcement_id, на который
    ссылаются Lot/Document/etc., но самого объявления в источнике нет.
    """
    orphan_ids: set[int] = set()
    # Lot.announcement_id — основной источник stub-ов (см. правило про
    # «кнопку Загрузить документы работает на stub-лотах»).
    for tbl_col in (Lot.announcement_id, Document.announcement_id):
        rows = src.execute(
            select(tbl_col).where(tbl_col.is_not(None))
            .where(~tbl_col.in_(select(Announcement.id)))
            .distinct()
        ).scalars().all()
        orphan_ids.update(rows)
    if not orphan_ids:
        log.info("orphan-announcements нет — пропускаю stub-фазу")
        return

    log.info("создаю %d stub-Announcement для orphan FK", len(orphan_ids))
    stub_rows = [
        {"id": aid, "url": f"https://goszakup.gov.kz/ru/announce/index/{aid}"}
        for aid in orphan_ids
    ]
    # Батчами, на всякий случай — если orphan-ов много (десятки тысяч),
    # один INSERT с тысячами VALUES в строке упадёт по limit'у Postgres.
    for i in range(0, len(stub_rows), BATCH_SIZE):
        batch = stub_rows[i : i + BATCH_SIZE]
        stmt = pg_insert(Announcement.__table__).values(batch).on_conflict_do_nothing(
            index_elements=[Announcement.__table__.primary_key.columns.values()[0]]
        )
        dst.execute(stmt)
    dst.commit()


def _bump_sequences(dst_engine) -> None:
    """Поднимаем postgres-sequences до max(id) во всех таблицах с Integer PK.

    Иначе следующий INSERT без явного id наступит на уже занятое значение и
    упадёт с unique-violation. Делаем по всем PK SERIAL-колонкам разом.
    """
    from sqlalchemy import text

    with dst_engine.begin() as conn:
        for model_cls in TABLES_IN_ORDER:
            table = model_cls.__tablename__
            pk_col = list(model_cls.__table__.primary_key.columns)[0].name
            row = conn.execute(text(f"SELECT MAX({pk_col}) FROM {table}")).first()
            max_id = (row[0] or 0) if row else 0
            if max_id <= 0:
                continue
            seq_name = f"{table}_{pk_col}_seq"
            # Не у всех таблиц есть sequence (BigInteger PK без autoincrement,
            # например lots/announcements — id берётся с goszakup). pg_class
            # подскажет, существует ли seq, чтобы не падать.
            exists = conn.execute(
                text("SELECT 1 FROM pg_class WHERE relkind='S' AND relname=:n"),
                {"n": seq_name},
            ).first()
            if not exists:
                continue
            conn.execute(text(f"SELECT setval('{seq_name}', :v)"), {"v": max_id})
            log.info("sequence %s → %s", seq_name, max_id)


if __name__ == "__main__":
    sys.exit(main())
