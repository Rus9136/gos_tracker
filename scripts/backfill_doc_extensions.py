"""Дописать расширения файлам, скачанным API-путём под хешем без расширения.

До фикса suggested_name (originalName из OWS `Files`) файлы ложились на диск
как `data/docs/{anno}/{md5-хеш}` — pick_tz_document отбирал кандидатов по
расширению и такие файлы не видел, лоты уходили в low-confidence без ТЗ.

Фаза 1: по магическим байтам переименовать файл (`<хеш>` → `<хеш>.pdf`)
и обновить Document.local_path. Фаза 2 (--reanalyze): переанализировать
актуальные лоты, у которых анализ прошёл без ТЗ (tz_sha256 IS NULL) —
идемпотентность сама пересчитает: новый sha ТЗ не совпадёт с NULL.

Запуск: .venv/bin/python scripts/backfill_doc_extensions.py [--dry-run] [--reanalyze [--limit N]]
"""

import argparse
import logging
import os
import time
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from goszakup.classify.llm import analyze_and_save
from goszakup.db.engine import SessionLocal
from goszakup.db.models import Announcement, Document, Lot, LotAnalysis

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill_ext")
log.setLevel(logging.INFO)


def _sniff_ext(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return None
    if head == b"%PDF":
        return "pdf"
    if head == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(path) as zf:
                if any(n.startswith("word/") for n in zf.namelist()):
                    return "docx"
        except Exception:
            return None
    return None


def rename_files(session, *, dry_run: bool) -> int:
    docs = session.scalars(
        select(Document).where(
            Document.local_path.isnot(None), Document.sha256.isnot(None)
        )
    ).all()
    renamed = 0
    skipped = 0
    for i, doc in enumerate(docs, 1):
        fname = os.path.basename(doc.local_path)
        if "." in fname:
            continue  # расширение уже есть
        if not os.path.exists(doc.local_path):
            skipped += 1
            continue
        ext = _sniff_ext(doc.local_path)
        if ext is None:
            log.info("не распознан формат: %s (%s)", doc.local_path, doc.name)
            skipped += 1
            continue
        new_path = f"{doc.local_path}.{ext}"
        if dry_run:
            log.info("[dry-run] %s → .%s", fname, ext)
            renamed += 1
            continue
        if os.path.exists(new_path):
            log.warning("цель уже существует, пропускаю: %s", new_path)
            skipped += 1
            continue
        os.rename(doc.local_path, new_path)
        doc.local_path = new_path
        renamed += 1
        if renamed % 200 == 0:
            session.commit()
            log.info("переименовано %d…", renamed)
    session.commit()
    log.info("итог фазы 1: переименовано %d, пропущено %d", renamed, skipped)
    return renamed


def reanalyze(session, *, limit: int | None) -> None:
    stmt = (
        select(Lot)
        .join(LotAnalysis, LotAnalysis.lot_id == Lot.id)
        .where(Lot.is_actual.is_(True), LotAnalysis.tz_sha256.is_(None))
        .options(
            selectinload(Lot.customer),
            selectinload(Lot.analysis),
            selectinload(Lot.announcement).selectinload(Announcement.documents),
        )
    )
    if limit:
        stmt = stmt.limit(limit)
    candidates = session.scalars(stmt).all()
    log.info("кандидатов на переанализ: %d", len(candidates))

    done = 0
    REQ_PACING = 1.5  # Cerebras free-tier душит бурсты (правило #13)
    for i, lot in enumerate(candidates, 1):
        if i > 1:
            time.sleep(REQ_PACING)
        if analyze_and_save(session, lot):
            session.commit()
            done += 1
        if i % 25 == 0:
            log.info("%d/%d, переанализировано %d", i, len(candidates), done)
    log.info("итог фазы 2: переанализировано %d из %d", done, len(candidates))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reanalyze", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    with SessionLocal() as session:
        rename_files(session, dry_run=args.dry_run)
        if args.reanalyze and not args.dry_run:
            reanalyze(session, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
