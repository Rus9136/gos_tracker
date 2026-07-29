"""Фаза B: фильтр по вертикали в списках, «Прочее», дашборд, валидация scope.

Работает под GZ_NO_AUTH=1 из conftest — синтетический dev-админ видит всё.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Lot, User
from goszakup.web.app import app


@pytest.fixture
def client():
    init_db()
    with SessionLocal() as s:
        s.query(Lot).delete()
        s.query(User).delete()
        s.commit()
    with TestClient(app) as c:
        yield c


def _mk_lot(lot_id, *, category, name=None, actual=True):
    with SessionLocal() as s:
        s.add(
            Lot(
                id=lot_id,
                url=f"https://goszakup.gov.kz/ru/announce/index/{lot_id}",
                name=name or f"Лот {lot_id}",
                kato="750000000",
                category=category,
                plan_amount=Decimal(1_000_000),
                status_code=210,
                is_actual=actual,
            )
        )
        s.commit()


def test_category_filter_by_slug(client):
    _mk_lot(1, category="it", name="Лот айти")
    _mk_lot(2, category="medicine", name="Лот медицина")
    body = client.get("/actual?category=medicine").text
    assert "Лот медицина" in body
    assert "Лот айти" not in body


def test_category_filter_other_is_null(client):
    _mk_lot(1, category="it", name="Лот айти")
    _mk_lot(2, category=None, name="Лот безвертикали")
    body = client.get("/actual?category=other").text
    assert "Лот безвертикали" in body
    assert "Лот айти" not in body
    # без фильтра видны оба
    body_all = client.get("/actual").text
    assert "Лот айти" in body_all and "Лот безвертикали" in body_all


def test_dashboard_verticals_breakdown(client):
    _mk_lot(1, category="medicine")
    _mk_lot(2, category=None)
    body = client.get("/").text
    assert "По вертикалям" in body
    assert "Медицина" in body
    assert "Прочее" in body
    # дриллдауны с дашборда ведут на списки с фильтром
    assert "/actual?category=medicine" in body
    assert "/actual?category=other" in body


def test_users_create_scope_validation(client):
    r = client.post(
        "/users",
        data={
            "username": "vasya",
            "password": "secret123",
            "categories": ["it", "bogus_slug", "other"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username == "vasya"))
        # мусор и псевдо-слаг other отброшены, остаются реальные вертикали
        assert u.categories == ["it"]


def test_users_create_empty_scope_is_none(client):
    r = client.post(
        "/users",
        data={"username": "petya", "password": "secret123"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username == "petya"))
        # пустой список = «без ограничения», хранится как NULL (правило #15)
        assert u.categories is None
