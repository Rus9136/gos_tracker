"""Fan-out матчинга: pre-filter по scope ДО постановки в очередь.

`match_actor.send` подменяется — Redis не нужен. Проверяем, что лот уходит
только владельцам запросов, в чей scope он попадает, и что dev-запросы
(user_id без строки в users, GZ_NO_AUTH) не теряются.
"""

from __future__ import annotations

import pytest

from goszakup.db.models import Announcement, Lot, User, UserQuery
from goszakup.queue import matching as matching_mod
from goszakup.queue.matching import enqueue_matches_for_lot, match_actor


def test_actor_queue_follows_naming_convention():
    assert match_actor.queue_name == "goszakup_matching"
    assert match_actor.options.get("time_limit")


@pytest.fixture
def sent(monkeypatch):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        matching_mod.match_actor,
        "send",
        lambda qid, lid, notify=False: calls.append((qid, lid)),
    )
    return calls


def _seed(db_session):
    db_session.add(Announcement(id=101, url="u/101"))
    lot = Lot(
        id=1, url="u/101", announcement_id=101,
        kato="751000000", it_category="software_dev", plan_amount=5_000_000,
    )
    db_session.add(lot)

    in_scope = User(username="almaty", password_hash="", regions=["751000000"])
    out_scope = User(username="shymkent", password_hash="", regions=["511000000"])
    inactive_owner = User(username="sleeper", password_hash="")
    db_session.add_all([in_scope, out_scope, inactive_owner])
    db_session.flush()

    q_in = UserQuery(user_id=in_scope.id, name="q1", text="1С")
    q_out = UserQuery(user_id=out_scope.id, name="q2", text="1С")
    q_off = UserQuery(user_id=inactive_owner.id, name="q3", text="1С", active=False)
    db_session.add_all([q_in, q_out, q_off])
    db_session.flush()
    return lot, q_in, q_out, q_off


def test_fanout_respects_scope_and_active(db_session, sent):
    lot, q_in, q_out, q_off = _seed(db_session)

    n = enqueue_matches_for_lot(db_session, lot)

    assert n == 1
    assert sent == [(q_in.id, lot.id)]


def test_fanout_passes_notify_true(db_session, monkeypatch):
    """Forward-поток (новый лот) ставит match_actor с notify=True —
    положительный матч превратится в Telegram-уведомление."""
    lot, *_ = _seed(db_session)
    captured: list[bool] = []
    monkeypatch.setattr(
        matching_mod.match_actor,
        "send",
        lambda qid, lid, notify=False: captured.append(notify),
    )

    enqueue_matches_for_lot(db_session, lot)

    assert captured == [True]


def test_fanout_keeps_dev_queries_without_user_row(db_session, sent):
    """GZ_NO_AUTH-админ имеет id=0 без строки в users — outerjoin не должен
    выкидывать его запросы (видит всё)."""
    lot, q_in, *_ = _seed(db_session)
    dev_q = UserQuery(user_id=0, name="dev", text="всё про 1С")
    db_session.add(dev_q)
    db_session.flush()

    n = enqueue_matches_for_lot(db_session, lot)

    assert n == 2
    assert set(sent) == {(q_in.id, lot.id), (dev_q.id, lot.id)}
