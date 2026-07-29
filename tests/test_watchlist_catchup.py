"""Догон watchlist: лоты, попавшие в него задним числом (jobs/watchlist_catchup)."""

from __future__ import annotations

from goszakup.db.models import Announcement, Lot, LotAnalysis, User
from goszakup.jobs import watchlist_catchup as catchup
from goszakup.jobs.watchlist_catchup import announcements_to_catchup


def _seed(db_session, lots):
    db_session.add(User(username="u", password_hash="", is_active=True,
                        categories=["it"]))
    for lot_id, anno_id, category, analyzed in lots:
        if db_session.get(Announcement, anno_id) is None:
            db_session.add(Announcement(id=anno_id, url=f"u/{anno_id}"))
            db_session.flush()
        db_session.add(Lot(id=lot_id, announcement_id=anno_id, url="u", name="Лот",
                           category=category, is_actual=True))
        db_session.flush()
        if analyzed:
            db_session.add(LotAnalysis(
                lot_id=lot_id, dev_category="other", analyzer_version="v1",
                analysis_confidence="high",
            ))
    db_session.flush()


def test_picks_unanalyzed_watchlist_lots_only(db_session):
    _seed(db_session, [
        (1, 10, "it", False),        # догоняем
        (2, 20, "it", True),         # уже разобран
        (3, 30, "medicine", False),  # вне watchlist
    ])
    assert announcements_to_catchup(db_session, 100) == [10]


def test_announcements_are_deduped_and_capped(db_session):
    _seed(db_session, [
        (1, 10, "it", False),
        (2, 10, "it", False),  # то же объявление
        (3, 20, "it", False),
    ])
    assert set(announcements_to_catchup(db_session, 100)) == {10, 20}
    assert len(announcements_to_catchup(db_session, 1)) == 1


def test_run_catchup_dry_run_sends_nothing(db_session, monkeypatch):
    _seed(db_session, [(1, 10, "it", False)])
    db_session.commit()
    from goszakup.queue import actors

    def _boom(*a, **kw):
        raise AssertionError("dry-run не должен ставить задачи")

    monkeypatch.setattr(actors.detail_actor, "send", _boom)
    assert catchup.run_catchup(dry_run=True) == 1
