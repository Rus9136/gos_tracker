"""Гейт 2 (P0 №6): неизвестный статус goszakup даёт активный сигнал.

Раньше незнакомое имя статуса молча → status_code=None → is_actual=False, и лот
тихо пропадал из /actual/матчинга. Теперь — WARNING (уходит в Sentry), дедуп по
имени статуса.
"""

from __future__ import annotations

import logging

from goszakup.db.models import Announcement
from goszakup.jobs import run_preset
from goszakup.scraper.search import ListingHit


def _hit(lot_id, status_name):
    return ListingHit(
        lot_id=lot_id, lot_number="1", announcement_id=200, announcement_number="A",
        announcement_url="u/200", lot_name="лот", customer_name="Орг", enstru="620000",
        quantity="1", plan_amount=1.0, amount_raw="1", method="m", status_name=status_name,
    )


def test_unknown_status_warns_and_deactivates(db_session, caplog):
    run_preset._warn_unknown_status.cache_clear()
    db_session.add(Announcement(id=200, url="u/200"))
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger="goszakup.jobs.run_preset"):
        lot = run_preset._upsert_lot_from_listing(
            db_session, _hit(1, "Совсем новый статус goszakup"),
            kato="750000000", on_new=[], on_status_change=[],
        )
    db_session.commit()

    assert lot.status_code is None
    assert lot.is_actual is False
    assert any("неизвестный статус" in r.message for r in caplog.records)


def test_known_status_does_not_warn(db_session, caplog):
    run_preset._warn_unknown_status.cache_clear()
    db_session.add(Announcement(id=200, url="u/200"))
    db_session.commit()

    from goszakup.scraper.statuses import STATUS_NAMES

    known = next(iter(STATUS_NAMES.values()))
    with caplog.at_level(logging.WARNING, logger="goszakup.jobs.run_preset"):
        run_preset._upsert_lot_from_listing(
            db_session, _hit(2, known), kato="750000000", on_new=[], on_status_change=[]
        )
    db_session.commit()
    assert not any("неизвестный статус" in r.message for r in caplog.records)


def test_unknown_status_deduped(db_session, caplog):
    run_preset._warn_unknown_status.cache_clear()
    db_session.add(Announcement(id=200, url="u/200"))
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger="goszakup.jobs.run_preset"):
        for lid in (10, 11, 12):
            run_preset._upsert_lot_from_listing(
                db_session, _hit(lid, "Одинаковый неизвестный"),
                kato="750000000", on_new=[], on_status_change=[],
            )
    db_session.commit()
    warns = [r for r in caplog.records if "неизвестный статус" in r.message]
    assert len(warns) == 1  # один WARNING на имя статуса, не на каждый лот
