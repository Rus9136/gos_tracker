"""Telegram-уведомления о новых матчах.

Проверяем:
- forward-поток (notify=True) ставит notify_actor только на положительный
  матч, ещё не отправленный; backfill (notify=False) — никогда;
- notify_actor идемпотентен по notified_at и уважает тумблер/наличие chat_id;
- send_message без токена и render — без сети.
"""

from __future__ import annotations

import pytest

from goszakup.classify import matcher
from goszakup.classify.matcher import MatchResult, _Outcome
from goszakup.db.models import Announcement, Lot, User, UserLotMatch, UserQuery


@pytest.fixture
def seeded(db_session):
    db_session.execute(UserLotMatch.__table__.delete())
    db_session.execute(UserQuery.__table__.delete())
    db_session.execute(User.__table__.delete())
    user = User(
        username="tg", password_hash="",
        telegram_chat_id="123456", notify_telegram=True,
    )
    db_session.add(user)
    db_session.flush()
    query = UserQuery(user_id=user.id, name="1С", text="разработка 1С")
    db_session.add(query)
    db_session.add(Announcement(id=101, url="https://goszakup.gov.kz/ru/announce/index/101"))
    lot = Lot(
        id=1, url="https://goszakup.gov.kz/ru/announce/index/101",
        announcement_id=101, name="Доработка 1С <ERP>", plan_amount=5_000_000,
        kato="751000000",
    )
    db_session.add(lot)
    db_session.commit()
    return user, query, lot


# === render ===

def test_render_contains_fields_and_escapes(seeded, db_session):
    from goszakup.notify.render import build_match_message

    user, query, lot = seeded
    match = UserLotMatch(
        user_query_id=query.id, lot_id=lot.id, matched=True, score=88,
        reason="подходит под 1С", matcher_version="v", query_version=1,
    )
    msg = build_match_message(query, lot, match)
    assert "88/100" in msg
    assert query.name in msg
    assert "/lot/1" in msg  # ссылка на карточку
    # `<ERP>` в названии должно быть экранировано, а не сломать HTML-разметку.
    assert "&lt;ERP&gt;" in msg
    assert "<ERP>" not in msg


# === telegram.send_message ===

def test_send_message_no_token(monkeypatch):
    import goszakup.notify.telegram as tg

    monkeypatch.setattr(tg, "GZ_TELEGRAM_BOT_TOKEN", None)
    ok, err = tg.send_message("123", "hi")
    assert ok is False
    assert "TOKEN" in (err or "")


# === notify_actor ===

def test_notify_actor_sends_and_is_idempotent(seeded, db_session, monkeypatch):
    import goszakup.queue.notify as notify_mod

    user, query, lot = seeded
    match = UserLotMatch(
        user_query_id=query.id, lot_id=lot.id, matched=True, score=80,
        reason="ок", matcher_version="v", query_version=1,
    )
    db_session.add(match)
    db_session.commit()

    sent: list[str] = []
    monkeypatch.setattr(
        notify_mod, "send_message",
        lambda chat_id, text, **kw: (sent.append(chat_id) or (True, None)),
    )

    notify_mod.notify_actor(match.id)
    db_session.expire_all()
    assert sent == ["123456"]
    assert db_session.get(UserLotMatch, match.id).notified_at is not None

    # Повтор — notified_at уже стоит, ничего не шлём.
    notify_mod.notify_actor(match.id)
    assert sent == ["123456"]


def test_notify_actor_skips_when_disabled(seeded, db_session, monkeypatch):
    import goszakup.queue.notify as notify_mod

    user, query, lot = seeded
    user.notify_telegram = False
    match = UserLotMatch(
        user_query_id=query.id, lot_id=lot.id, matched=True, score=80,
        reason="ок", matcher_version="v", query_version=1,
    )
    db_session.add(match)
    db_session.commit()

    sent: list[str] = []
    monkeypatch.setattr(
        notify_mod, "send_message",
        lambda chat_id, text, **kw: (sent.append(chat_id) or (True, None)),
    )

    notify_mod.notify_actor(match.id)
    db_session.expire_all()
    assert sent == []
    # notified_at проставлен, чтобы не дёргать повторно.
    assert db_session.get(UserLotMatch, match.id).notified_at is not None


# === match_actor: enqueue only on positive forward match ===

def test_match_actor_enqueues_notify_on_positive(seeded, db_session, monkeypatch):
    import goszakup.queue.matching as matching_mod
    import goszakup.queue.notify as notify_mod

    user, query, lot = seeded
    monkeypatch.setattr(
        matcher, "_call_llm",
        lambda qt, lt: _Outcome(MatchResult(matched=True, score=85, reason="ок")),
    )
    enq: list[int] = []
    monkeypatch.setattr(notify_mod.notify_actor, "send", lambda mid: enq.append(mid))

    matching_mod.match_actor(query.id, lot.id, notify=True)

    db_session.expire_all()
    row = db_session.query(UserLotMatch).one()
    assert row.matched is True
    assert enq == [row.id]


def test_match_actor_no_notify_on_negative(seeded, db_session, monkeypatch):
    import goszakup.queue.matching as matching_mod
    import goszakup.queue.notify as notify_mod

    user, query, lot = seeded
    monkeypatch.setattr(
        matcher, "_call_llm",
        lambda qt, lt: _Outcome(MatchResult(matched=False, score=10, reason="нет")),
    )
    enq: list[int] = []
    monkeypatch.setattr(notify_mod.notify_actor, "send", lambda mid: enq.append(mid))

    matching_mod.match_actor(query.id, lot.id, notify=True)
    assert enq == []


def test_match_actor_backfill_never_notifies(seeded, db_session, monkeypatch):
    import goszakup.queue.matching as matching_mod
    import goszakup.queue.notify as notify_mod

    user, query, lot = seeded
    monkeypatch.setattr(
        matcher, "_call_llm",
        lambda qt, lt: _Outcome(MatchResult(matched=True, score=95, reason="ок")),
    )
    enq: list[int] = []
    monkeypatch.setattr(notify_mod.notify_actor, "send", lambda mid: enq.append(mid))

    # notify=False (как из enqueue_matches_for_query / backfill).
    matching_mod.match_actor(query.id, lot.id, notify=False)
    assert enq == []
