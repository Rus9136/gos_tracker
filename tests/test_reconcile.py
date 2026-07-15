"""Гейт 2: self-healing дозаполнение объявлений-заглушек.

Оборванный листинг оставляет актуальные лоты со stub-объявлением (number NULL)
без деталей — на ретрае они «не новые», детали не приедут. reconcile их находит
и переставляет detail_actor.
"""

from __future__ import annotations

from goszakup.db.models import Announcement, Lot
from goszakup.jobs.reconcile import find_orphan_stub_annos


def _add(db, anno_id, number, actual):
    db.add(Announcement(id=anno_id, url=f"u/{anno_id}", number=number))
    db.add(Lot(id=anno_id, announcement_id=anno_id, url=f"u/{anno_id}", is_actual=actual))


def test_finds_only_actual_stubs(db_session):
    _add(db_session, 1, None, True)      # заглушка + актуальный → цель
    _add(db_session, 2, "A-2", True)     # детализировано → нет
    _add(db_session, 3, None, False)     # заглушка, но лот не актуален → нет
    db_session.commit()
    assert find_orphan_stub_annos(db_session) == [1]


def test_dedup_multiple_lots_same_stub(db_session):
    db_session.add(Announcement(id=1, url="u/1", number=None))
    db_session.add_all([
        Lot(id=1, announcement_id=1, url="u/1", is_actual=True),
        Lot(id=2, announcement_id=1, url="u/1", is_actual=True),
    ])
    db_session.commit()
    assert find_orphan_stub_annos(db_session) == [1]


def test_limit_respected(db_session):
    for i in range(1, 6):
        _add(db_session, i, None, True)
    db_session.commit()
    assert len(find_orphan_stub_annos(db_session, limit=3)) == 3


def test_reconcile_actor_enqueues_details(db_session, monkeypatch):
    from goszakup.queue import actors

    _add(db_session, 1, None, True)
    _add(db_session, 2, None, True)
    db_session.commit()

    sent: list = []
    monkeypatch.setattr(actors, "_redis_client", lambda: object())
    monkeypatch.setattr(actors, "_set_pending", lambda r, run_id, n: None)
    monkeypatch.setattr(actors.detail_actor, "send", lambda *a, **k: sent.append(a))

    actors.reconcile_actor()  # dramatiq Actor вызывается синхронно

    assert sorted(a[0] for a in sent) == [1, 2]


def test_reconcile_actor_noop_without_orphans(db_session, monkeypatch):
    from goszakup.queue import actors

    _add(db_session, 1, "A-1", True)  # уже детализировано
    db_session.commit()

    sent: list = []
    monkeypatch.setattr(actors, "_redis_client", lambda: object())
    monkeypatch.setattr(actors, "_set_pending", lambda r, run_id, n: None)
    monkeypatch.setattr(actors.detail_actor, "send", lambda *a, **k: sent.append(a))

    actors.reconcile_actor()
    assert sent == []
