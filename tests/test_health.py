"""Сторож LLM-контура (jobs/health.py).

Смысл сторожа — активный сигнал там, где правило #7 намеренно молчит.
Поэтому проверяем ровно то, ради чего он написан: отказ провайдера виден,
здоровый контур не поднимает ложную тревогу, а алерт не спамит.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from goszakup.db.models import Announcement, Lot, User, UserLotMatch, UserQuery
from goszakup.jobs import health


@pytest.fixture
def seeded(db_session):
    db_session.execute(UserLotMatch.__table__.delete())
    db_session.execute(UserQuery.__table__.delete())
    db_session.execute(User.__table__.delete())
    admin = User(
        username="root", password_hash="", is_admin=True,
        telegram_chat_id="42", notify_telegram=True,
    )
    plain = User(
        username="user", password_hash="", is_admin=False,
        telegram_chat_id="99", notify_telegram=True,
    )
    db_session.add_all([admin, plain])
    db_session.flush()
    query = UserQuery(user_id=admin.id, name="q", text="разработка")
    db_session.add(query)
    db_session.add(Announcement(id=1, url="https://goszakup.gov.kz/ru/announce/index/1"))
    db_session.add(Lot(id=1, url="https://goszakup.gov.kz/ru/announce/index/1",
                       announcement_id=1, name="лот"))
    db_session.flush()
    db_session.add(UserLotMatch(
        user_query_id=query.id, lot_id=1, matched=True, score=90,
        matched_at=datetime.now(UTC), matcher_version="m1", query_version=1,
    ))
    db_session.commit()
    return db_session


def test_llm_down_is_reported(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (False, "402 payment_required"))
    problems = health.collect_problems(seeded)
    assert any("Cerebras" in p and "402" in p for p in problems)


def test_healthy_llm_and_fresh_match_report_nothing(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    assert health.collect_problems(seeded) == []


def test_stale_match_is_reported(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    seeded.query(UserLotMatch).update(
        {UserLotMatch.matched_at: datetime.now(UTC) - timedelta(hours=100)}
    )
    seeded.flush()
    problems = health.collect_problems(seeded)
    assert any("матч" in p for p in problems)


def test_stale_check_can_be_disabled(seeded, monkeypatch):
    # 0 — выключатель: у кого матчи редки, эта проверка только шумит.
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    monkeypatch.setattr(health, "MATCH_STALE_HOURS", 0)
    seeded.query(UserLotMatch).update(
        {UserLotMatch.matched_at: datetime.now(UTC) - timedelta(hours=100)}
    )
    seeded.flush()
    assert health.collect_problems(seeded) == []


def test_alert_goes_only_to_admins(seeded):
    # Обычный юзер с chat_id не должен получать операционные алерты.
    assert health._admin_chat_ids(seeded) == ["42"]


def test_no_matches_at_all_is_not_an_alarm(seeded, monkeypatch):
    # Пустая база (новый инстанс) — не повод будить админа.
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    seeded.execute(UserLotMatch.__table__.delete())
    seeded.flush()
    assert health.match_age_hours(seeded) is None
    assert health.collect_problems(seeded) == []


def test_alert_is_sent_and_deduped(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (False, "402"))
    monkeypatch.setattr(health, "GZ_TELEGRAM_BOT_TOKEN", "token")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "goszakup.notify.telegram.send_message",
        lambda chat_id, text, **kw: (sent.append((chat_id, text)), (True, None))[1],
    )

    allowed = iter([True, False])
    monkeypatch.setattr(health, "_alert_allowed", lambda: next(allowed))

    health.run_health_check(seeded, notify=True)
    assert len(sent) == 1  # ушло админу

    health.run_health_check(seeded, notify=True)
    assert len(sent) == 1  # второй раз подавлен cooldown'ом — не спамим


def test_notify_false_never_sends(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (False, "402"))
    monkeypatch.setattr(health, "GZ_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(health, "_alert_allowed", lambda: pytest.fail("не должно дойти до дедупа"))
    problems = health.run_health_check(seeded, notify=False)
    assert problems  # проблему вернул…
