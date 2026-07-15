"""Гейт 2: retention скачанных ТЗ — удаление файлов старых прошедших лотов."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from goszakup.db.models import Announcement, Document, Lot
from goszakup.jobs.retention import cleanup_old_documents


def _mk_doc(db, tmp_path, *, anno_id, actual, downloaded_days_ago):
    db.add(Announcement(id=anno_id, url=f"u/{anno_id}", number="A"))
    db.add(Lot(id=anno_id, announcement_id=anno_id, url=f"u/{anno_id}", is_actual=actual))
    f = tmp_path / f"tz_{anno_id}.pdf"
    f.write_bytes(b"x" * 1000)
    db.add(Document(
        id=anno_id, announcement_id=anno_id, name="ТЗ", url=f"v3bl/{anno_id}",
        local_path=str(f), downloaded_at=datetime.now(UTC) - timedelta(days=downloaded_days_ago),
    ))
    return f


def test_old_past_document_removed(db_session, tmp_path):
    f = _mk_doc(db_session, tmp_path, anno_id=1, actual=False, downloaded_days_ago=200)
    db_session.commit()

    removed, freed = cleanup_old_documents(db_session, older_than_days=90)

    assert removed == 1
    assert freed == 1000
    assert not f.exists()
    assert db_session.get(Document, 1).local_path is None  # строка осталась, путь обнулён


def test_actual_lot_document_kept(db_session, tmp_path):
    f = _mk_doc(db_session, tmp_path, anno_id=2, actual=True, downloaded_days_ago=200)
    db_session.commit()

    removed, _ = cleanup_old_documents(db_session, older_than_days=90)

    assert removed == 0
    assert f.exists()  # у актуального лота ТЗ не трогаем


def test_recent_document_kept(db_session, tmp_path):
    f = _mk_doc(db_session, tmp_path, anno_id=3, actual=False, downloaded_days_ago=10)
    db_session.commit()

    removed, _ = cleanup_old_documents(db_session, older_than_days=90)

    assert removed == 0
    assert f.exists()  # свежий документ не трогаем
