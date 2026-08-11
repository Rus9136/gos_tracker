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


@pytest.fixture(autouse=True)
def _healthy_infra(monkeypatch):
    # По умолчанию Redis/диск здоровы — иначе новые проверки шумели бы во всех
    # существующих тестах collect_problems. Тесты про них переопределяют это.
    monkeypatch.setattr(health, "check_redis", lambda: (True, None))
    monkeypatch.setattr(health, "disk_free_gb", lambda p: 100.0)


@pytest.fixture
def seeded(db_session):
    db_session.execute(UserLotMatch.__table__.delete())
    db_session.execute(UserQuery.__table__.delete())
    db_session.execute(User.__table__.delete())
    admin = User(
        username="root", password_hash="", is_admin=True,
        telegram_chat_id="42", notify_telegram=True,
    )
    # Вертикаль подписчика обязательна: без неё watchlist пуст, и это само
    # по себе проблема (документы и LLM выключены для всего рынка).
    plain = User(
        username="user", password_hash="", is_admin=False,
        telegram_chat_id="99", notify_telegram=True, categories=["it"],
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


def test_empty_watchlist_is_reported(seeded, monkeypatch):
    """Пустой watchlist не даёт ошибок — только нули в счётчиках, поэтому
    заметить его может только сторож."""
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    seeded.execute(User.__table__.update().values(categories=None))
    seeded.flush()
    problems = health.collect_problems(seeded)
    assert any("Watchlist пуст" in p for p in problems)


def test_alert_is_sent_and_deduped(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (False, "402"))
    monkeypatch.setattr(health, "GZ_TELEGRAM_BOT_TOKEN", "token")
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "goszakup.notify.telegram.send_message",
        lambda chat_id, text, **kw: (sent.append((chat_id, text)), (True, None))[1],
    )

    seen: set[str] = set()
    monkeypatch.setattr(
        health, "_alertable",
        lambda ps: [p for p in ps if p.code not in seen and not seen.add(p.code)],
    )

    health.run_health_check(seeded, notify=True)
    assert len(sent) == 1  # ушло админу

    health.run_health_check(seeded, notify=True)
    assert len(sent) == 1  # второй раз подавлен cooldown'ом — не спамим


def test_new_problem_breaks_cooldown_of_another(seeded, monkeypatch):
    """Регрессия 2026-08-11: общий ключ дедупа на весь прогон означал, что
    вторая, независимая проблема попадала под чужой cooldown и не уходила
    вовсе. Окно cooldown у каждого вида проблемы своё."""
    monkeypatch.setattr(health, "GZ_TELEGRAM_BOT_TOKEN", "token")
    sent: list[str] = []
    monkeypatch.setattr(
        "goszakup.notify.telegram.send_message",
        lambda chat_id, text, **kw: (sent.append(text), (True, None))[1],
    )
    # Redis недоступен → _alertable пропускает всё; дедуп эмулируем сами.
    seen: set[str] = set()
    monkeypatch.setattr(
        health, "_alertable",
        lambda ps: [p for p in ps if p.code not in seen and not seen.add(p.code)],
    )

    monkeypatch.setattr(health, "check_llm", lambda: (False, "402"))
    health.run_health_check(seeded, notify=True)
    assert len(sent) == 1 and "Cerebras" in sent[0]

    # LLM всё ещё лежит (о ней уже сообщили), но добавилась вторая проблема.
    monkeypatch.setattr(health, "check_redis", lambda: (False, "ConnectionError"))
    health.run_health_check(seeded, notify=True)
    assert len(sent) == 2, "новая проблема обязана пробить чужой cooldown"
    assert "Redis" in sent[1] and "Cerebras" not in sent[1]


def test_cooldown_not_burned_without_recipients(seeded, monkeypatch):
    """Без админов с chat_id слать некуда — ключи cooldown ставить нельзя,
    иначе проблема замолчала бы на всё окно, никого не разбудив."""
    monkeypatch.setattr(health, "check_llm", lambda: (False, "402"))
    monkeypatch.setattr(health, "GZ_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(health, "_admin_chat_ids", lambda s: [])
    monkeypatch.setattr(
        health, "_alertable", lambda ps: pytest.fail("дедуп до проверки получателей")
    )
    assert health.run_health_check(seeded, notify=True)


def test_alertable_keys_cooldown_per_problem_code(monkeypatch):
    keys: dict[str, str] = {}

    class _FakeRedis:
        def set(self, key, value, nx=False, ex=None):  # noqa: ARG002
            if nx and key in keys:
                return None
            keys[key] = value
            return True

    monkeypatch.setattr(
        "redis.Redis.from_url", staticmethod(lambda *a, **kw: _FakeRedis())
    )
    llm = health.Problem("llm-down", "❌ LLM лежит")
    redis_down = health.Problem("redis-down", "❌ Redis лежит")

    assert health._alertable([llm]) == [llm]
    assert list(keys) == [f"{health._ALERT_KEY}:llm-down"]
    # Повтор той же проблемы молчит, соседняя проходит своим ключом.
    assert health._alertable([llm, redis_down]) == [redis_down]


def test_alertable_sends_everything_when_redis_is_down(monkeypatch):
    # Тишина дороже лишнего сообщения: без дедупа шлём всё.
    def _boom(*a, **kw):
        raise ConnectionError("нет redis")

    monkeypatch.setattr("redis.Redis.from_url", staticmethod(_boom))
    problems = [health.Problem("llm-down", "❌ LLM лежит")]
    assert health._alertable(problems) == problems


def test_notify_false_never_sends(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (False, "402"))
    monkeypatch.setattr(health, "GZ_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(health, "_alertable", lambda ps: pytest.fail("не должно дойти до дедупа"))
    problems = health.run_health_check(seeded, notify=False)
    assert problems  # проблему вернул…


# --- Лицензия Tumar (P1) ---------------------------------------------------

_NOW = datetime(2026, 7, 15, tzinfo=UTC)


def test_tumar_license_expired(monkeypatch):
    monkeypatch.setattr(health, "TUMAR_LICENSE_EXPIRES", "2026-07-01")
    p = health.tumar_license_problem(now=_NOW)
    assert p and "истекла" in p


def test_tumar_license_warns_before_expiry(monkeypatch):
    monkeypatch.setattr(health, "TUMAR_LICENSE_EXPIRES", "2026-07-20")
    monkeypatch.setattr(health, "TUMAR_LICENSE_WARN_DAYS", 14)
    p = health.tumar_license_problem(now=_NOW)
    assert p and "истекает через" in p


def test_tumar_license_valid_far(monkeypatch):
    monkeypatch.setattr(health, "TUMAR_LICENSE_EXPIRES", "2027-01-01")
    assert health.tumar_license_problem(now=_NOW) is None


def test_tumar_license_disabled(monkeypatch):
    monkeypatch.setattr(health, "TUMAR_LICENSE_EXPIRES", "")
    assert health.tumar_license_problem(now=_NOW) is None


def test_redis_down_is_reported(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    monkeypatch.setattr(health, "check_redis", lambda: (False, "ConnectionError: refused"))
    problems = health.collect_problems(seeded)
    assert any("Redis" in p for p in problems)


def test_low_disk_is_reported(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    monkeypatch.setattr(health, "disk_free_gb", lambda p: 0.2)
    monkeypatch.setattr(health, "DISK_MIN_FREE_GB", 1.0)
    problems = health.collect_problems(seeded)
    assert any("Мало места" in p for p in problems)


def test_ample_disk_not_reported(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    monkeypatch.setattr(health, "disk_free_gb", lambda p: 50.0)
    monkeypatch.setattr(health, "DISK_MIN_FREE_GB", 1.0)
    assert not any("Мало места" in p for p in health.collect_problems(seeded))


def test_tumar_gated_on_autosubmit_configured(seeded, monkeypatch):
    monkeypatch.setattr(health, "check_llm", lambda: (True, None))
    monkeypatch.setattr(health, "TUMAR_LICENSE_EXPIRES", "2026-07-01")  # истекла

    # Автоподача не сконфигурирована → лицензию не проверяем (нет шума).
    monkeypatch.setattr(health, "AUTOSUBMIT_AGENT_URL", None)
    assert not any("Tumar" in p for p in health.collect_problems(seeded))

    # Автоподача сконфигурирована → проблема поднимается.
    monkeypatch.setattr(health, "AUTOSUBMIT_AGENT_URL", "http://agent")
    assert any("Tumar" in p for p in health.collect_problems(seeded))
