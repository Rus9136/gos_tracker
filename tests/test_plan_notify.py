"""Уведомления о новых пунктах плана: гейты, пре-фильтр, дедуп, анти-лавина."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import PlanNotification, PlanPoint, User, UserQuery
from goszakup.jobs.plan_notify import notify_new_plan_points

NOW = datetime(2026, 8, 6, 12, tzinfo=UTC)


@pytest.fixture
def session():
    init_db()
    with SessionLocal() as s:
        s.query(PlanNotification).delete()
        s.query(UserQuery).delete()
        s.query(PlanPoint).delete()
        s.query(User).delete()
        s.commit()
        yield s


class _Sender:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent: list[tuple[str, str]] = []

    def __call__(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return (self.ok, None if self.ok else "boom")


def _user(session, **kw):
    fields = {
        "username": "u", "password_hash": "x", "is_admin": False,
        "telegram_chat_id": "42", "notify_telegram": True, "notify_plan": True,
    }
    fields.update(kw)
    u = User(**fields)
    session.add(u)
    session.commit()
    return u


_DEFAULT_PF = {"keywords": ["сервер"]}


def _query(session, user, pf=_DEFAULT_PF, **kw):
    q = UserQuery(
        user_id=user.id, name="Серверы", text="нужны серверы",
        active=True, compiled_filters=pf,
        **kw,
    )
    session.add(q)
    session.commit()
    return q


def _point(session, root_id, *, created_at=None, **kw):
    fields = {
        "point_id": root_id, "year": 2026, "customer_bin": "123456789012",
        "customer_name": "ГУ Тест", "name": "Сервер стоечный",
        "category": "it", "amount": Decimal(5_000_000), "month": 9,
        "trade_method_id": 2, "trade_method": "Открытый конкурс",
        "status_id": 2, "status_name": "Утвержден", "kato": "750000000",
        "created_at": created_at or (NOW - timedelta(hours=5)),
    }
    fields.update(kw)
    p = PlanPoint(root_id=root_id, **fields)
    session.add(p)
    session.commit()
    return p


def test_sends_and_dedupes(session):
    user = _user(session)
    _query(session, user)
    _point(session, 1)
    sender = _Sender()

    stats = notify_new_plan_points(session, now=NOW, sender=sender)
    assert stats.sent == 1
    assert "Сервер стоечный" in sender.sent[0][1]

    # Повторный прогон молчит — строка PlanNotification уже есть.
    stats = notify_new_plan_points(session, now=NOW, sender=sender)
    assert stats.sent == 0 and len(sender.sent) == 1


def test_prefilter_and_scope_gate(session):
    user = _user(session, regions=["750000000"], categories=["it"])
    _query(session, user, pf={"keywords": ["сервер"]})
    _point(session, 1, name="Бумага А4")  # не проходит по ключевому слову
    _point(session, 2, name="Сервер", kato="710000000")  # чужой регион
    _point(session, 3, name="Сервер", category="medicine")  # чужая вертикаль
    sender = _Sender()

    assert notify_new_plan_points(session, now=NOW, sender=sender).sent == 0


def test_no_notify_without_toggle_or_prefilter(session):
    off = _user(session, notify_plan=False)
    _query(session, off)
    _point(session, 1)
    sender = _Sender()
    assert notify_new_plan_points(session, now=NOW, sender=sender).sent == 0

    # Запрос без пре-фильтра не уведомляет ни о чём: у пункта плана нет ТЗ,
    # а по одному NL-тексту отбирать нечем.
    off.notify_plan = True
    off.username = "on"
    session.query(UserQuery).delete()
    _query(session, off, pf=None)
    session.commit()
    assert notify_new_plan_points(session, now=NOW, sender=sender).sent == 0


def test_backfill_does_not_flood(session):
    """Стартовый залив года молчит: пункты созданы в источнике давно."""
    user = _user(session)
    _query(session, user)
    _point(session, 1, created_at=NOW - timedelta(days=40))
    _point(session, 2, created_at=NOW - timedelta(days=4))
    sender = _Sender()
    assert notify_new_plan_points(session, now=NOW, sender=sender).sent == 0


def test_announced_points_are_skipped(session):
    user = _user(session)
    _query(session, user)
    _point(session, 1, status_id=5, status_name="Опубликован")
    sender = _Sender()
    assert notify_new_plan_points(session, now=NOW, sender=sender).sent == 0


def test_failed_delivery_is_recorded_not_retried(session):
    user = _user(session)
    _query(session, user)
    _point(session, 1)
    sender = _Sender(ok=False)

    stats = notify_new_plan_points(session, now=NOW, sender=sender)
    assert stats.sent == 0 and stats.skipped == 1
    row = session.query(PlanNotification).one()
    assert row.error == "boom"
    # Второй прогон не дёргает Telegram снова.
    notify_new_plan_points(session, now=NOW, sender=sender)
    assert len(sender.sent) == 1
