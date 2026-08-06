"""Витрина /plans: дефолты фильтров, scope пользователя, врезки в отчётах."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Announcement, Lot, Organization, PlanPoint, User
from goszakup.jobs.plan_report import (
    EMARKET_METHOD_ID,
    PlanFilters,
    org_plan_summary,
    plan_query,
    upcoming_summary,
)
from goszakup.scope import plan_scope_conditions
from goszakup.web.app import app

YEAR = datetime.now(UTC).year


@pytest.fixture
def session():
    init_db()
    with SessionLocal() as s:
        s.query(PlanPoint).delete()
        s.query(Lot).delete()
        s.query(Announcement).delete()
        s.query(Organization).delete()
        s.query(User).delete()
        s.commit()
        yield s


def _point(session, root_id, **kw):
    fields = {
        "point_id": root_id,
        "year": YEAR,
        "customer_bin": "123456789012",
        "customer_name": "ГУ Тест",
        "name": f"Пункт {root_id}",
        "category": "it",
        "amount": Decimal(1_000_000),
        "month": 8,
        "trade_method_id": 2,
        "trade_method": "Открытый конкурс",
        "status_id": 2,
        "status_name": "Утвержден",
        "kato": "750000000",
    }
    fields.update(kw)
    p = PlanPoint(root_id=root_id, **fields)
    session.add(p)
    session.commit()
    return p


def test_default_filters_hide_announced_and_emarket(session):
    _point(session, 1)  # в плане, конкурс
    _point(session, 2, status_id=5, status_name="Опубликован")  # уже объявлен
    _point(session, 3, trade_method_id=EMARKET_METHOD_ID, trade_method="Электронный магазин")

    rows = session.scalars(plan_query(PlanFilters(year=YEAR), [])).all()
    assert [r.root_id for r in rows] == [1]

    announced = session.scalars(
        plan_query(PlanFilters(year=YEAR, stage="announced"), [])
    ).all()
    assert [r.root_id for r in announced] == [2]

    with_em = session.scalars(
        plan_query(PlanFilters(year=YEAR, with_emarket=True), [])
    ).all()
    assert {r.root_id for r in with_em} == {1, 3}


def test_scope_limits_plan_like_lots(session):
    _point(session, 1, kato="750000000", category="it")
    _point(session, 2, kato="710000000", category="it")
    _point(session, 3, kato="750000000", category="medicine")
    user = User(
        username="u", password_hash="x", is_admin=False,
        regions=["750000000"], categories=["it"],
    )
    session.add(user)
    session.commit()

    rows = session.scalars(
        plan_query(PlanFilters(year=YEAR), plan_scope_conditions(user))
    ).all()
    assert [r.root_id for r in rows] == [1]
    # Админ видит всё — условий scope нет.
    admin = User(username="a", password_hash="x", is_admin=True)
    assert plan_scope_conditions(admin) == []


def test_upcoming_summary_counts_current_and_next_month(session):
    now = datetime(YEAR, 3, 15, tzinfo=UTC)
    _point(session, 1, month=3, amount=Decimal(100))
    _point(session, 2, month=4, amount=Decimal(200))
    _point(session, 3, month=9, amount=Decimal(400))
    _point(session, 4, month=3, amount=Decimal(800), status_id=5)  # уже объявлен

    s = upcoming_summary(session, [], now=now)
    assert s["n"] == 2
    assert s["total"] == 300.0


def test_org_plan_summary_splits_planned_and_announced(session):
    _point(session, 1, amount=Decimal(100))
    _point(session, 2, amount=Decimal(300), status_id=5, status_name="Опубликован")
    _point(session, 3, amount=Decimal(700), customer_bin="999999999999")

    s = org_plan_summary(session, ["123456789012"], year=YEAR)
    assert s["total_n"] == 2 and s["total_sum"] == 400.0
    assert s["planned_n"] == 1 and s["planned_sum"] == 100.0
    assert s["announced_n"] == 1 and s["announced_sum"] == 300.0
    assert [p.root_id for p in s["top"]] == [1]
    assert org_plan_summary(session, [], year=YEAR)["total_n"] == 0


def test_plans_page_renders_and_filters(session):
    _point(session, 1, name="Сопровождение ИС")
    _point(session, 2, name="Ноутбуки", category="hardware" )
    with TestClient(app) as c:
        r = c.get("/plans")
        assert r.status_code == 200
        assert "Сопровождение ИС" in r.text

        r = c.get("/plans?q=Ноутбук")
        assert "Ноутбуки" in r.text and "Сопровождение ИС" not in r.text

        r = c.get("/plans?format=csv")
        assert r.status_code == 200
        assert "БИН заказчика" in r.text


def test_org_card_has_plan_tab(session):
    org = Organization(id=7, bin="123456789012", name="ГУ Тест")
    session.add(org)
    _point(session, 1, name="Сопровождение ИС", amount=Decimal(9_000_000))
    _point(session, 2, name="Уже объявленный", status_id=5, status_name="Опубликован")
    _point(session, 3, name="Чужой пункт", customer_bin="999999999999")
    session.commit()

    with TestClient(app) as c:
        r = c.get("/organization/7")
        assert r.status_code == 200
        assert "План закупок" in r.text
        assert "Сопровождение ИС" in r.text
        # Вкладка показывает весь план заказчика, но не чужой.
        assert "Уже объявленный" in r.text
        assert "Чужой пункт" not in r.text


def test_org_without_bin_shows_empty_plan_tab(session):
    session.add(Organization(id=8, name="Без БИН"))
    session.commit()
    with TestClient(app) as c:
        r = c.get("/organization/8")
        assert r.status_code == 200
        assert "не заполнен БИН" in r.text


def test_lot_card_shows_plan_point(session):
    _point(session, 87491617, name="Сопровождение ИС", prepayment=30.0)
    session.add(Announcement(id=500, url="https://x/500"))
    session.add(
        Lot(
            id=42, number="87491617-ОК1", announcement_id=500,
            name="Сопровождение ИС", url="https://x/500",
            plan_amount=Decimal(1_000_000), category="it", kato="750000000",
        )
    )
    session.commit()
    with TestClient(app) as c:
        r = c.get("/lot/42")
        assert r.status_code == 200
        assert "Пункт годового плана" in r.text
        assert "30 %" in r.text
