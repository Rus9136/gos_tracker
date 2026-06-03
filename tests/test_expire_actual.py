"""Парсинг дедлайна приёма заявок + снятие просроченных лотов с актуальных."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from goszakup.db.models import Announcement, Lot
from goszakup.jobs.expire import expire_actual_lots
from goszakup.scraper.announce import _find_deadline, _parse_deadline


def test_parse_deadline_localizes_almaty_to_utc():
    # 18:00 по Алматы (UTC+5) == 13:00 UTC.
    d = _parse_deadline("10.06.2026 18:00:00")
    assert d == datetime(2026, 6, 10, 13, 0, tzinfo=UTC)


def test_find_deadline_matches_label_variants():
    assert _find_deadline({"Дата и время окончания приёма заявок": "10.06.2026 18:00"})
    assert _find_deadline({"Срок окончания приема заявок": "10.06.2026 18:00"})
    # аукцион — ценовые предложения
    assert _find_deadline({"Дата окончания приема ценовых предложений": "10.06.2026 09:30"})
    # начало приёма — НЕ дедлайн
    assert _find_deadline({"Дата начала приема заявок": "01.06.2026 09:00"}) is None


def _mk_lot(db_session, lot_id, anno_id, *, application_end, is_actual=True):
    db_session.add(Announcement(id=anno_id, url=f"u/{anno_id}", application_end=application_end))
    db_session.flush()
    lot = Lot(id=lot_id, url=f"u/{anno_id}", announcement_id=anno_id, is_actual=is_actual)
    db_session.add(lot)
    db_session.flush()
    return lot


def test_expire_flips_past_deadline_only(db_session):
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    past = _mk_lot(db_session, 1, 101, application_end=now - timedelta(hours=1))
    future = _mk_lot(db_session, 2, 102, application_end=now + timedelta(days=1))
    no_deadline = _mk_lot(db_session, 3, 103, application_end=None)

    n = expire_actual_lots(db_session, now=now)

    assert n == 1
    db_session.refresh(past)
    db_session.refresh(future)
    db_session.refresh(no_deadline)
    assert past.is_actual is False
    assert future.is_actual is True
    assert no_deadline.is_actual is True  # без дедлайна не трогаем


def test_expire_is_idempotent(db_session):
    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    _mk_lot(db_session, 1, 101, application_end=now - timedelta(hours=1))
    assert expire_actual_lots(db_session, now=now) == 1
    assert expire_actual_lots(db_session, now=now) == 0
