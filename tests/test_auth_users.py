"""Аутентификация, scope-изоляция и admin-only доступ.

conftest ставит GZ_NO_AUTH=1 (dev-режим без логина), поэтому здесь явно
включаем реальную аутентификацию через monkeypatch `_AUTH_DISABLED=False`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Lot, User
from goszakup.web import auth as auth_mod
from goszakup.web.app import app
from goszakup.web.auth import hash_password


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_mod, "_AUTH_DISABLED", False)
    init_db()
    with SessionLocal() as s:
        s.query(Lot).delete()
        s.query(User).delete()
        s.commit()
    with TestClient(app) as c:
        yield c


def _mk_user(username, password, *, is_admin=False, regions=None, categories=None, min_amount=None):
    with SessionLocal() as s:
        u = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
            is_active=True,
            regions=regions,
            categories=categories,
            min_amount=min_amount,
        )
        s.add(u)
        s.commit()
        return u.id


def _mk_lot(lot_id, *, kato, category, amount, actual=True):
    with SessionLocal() as s:
        s.add(
            Lot(
                id=lot_id,
                url=f"https://goszakup.gov.kz/ru/announce/index/{lot_id}",
                name=f"Лот {lot_id}",
                kato=kato,
                category=category,
                plan_amount=Decimal(amount),
                status_code=210,
                is_actual=actual,
            )
        )
        s.commit()


def _login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_password_hash_roundtrip():
    h = hash_password("сложный-пароль-😀")
    assert auth_mod.verify_password("сложный-пароль-😀", h)
    assert not auth_mod.verify_password("другой", h)


def test_unauthenticated_redirects_to_login(client):
    r = client.get("/actual", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_login_wrong_password(client):
    _mk_user("alice", "secret123")
    r = _login(client, "alice", "WRONG")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?error=1"


def test_login_then_access(client):
    _mk_user("alice", "secret123")
    r = _login(client, "alice", "secret123")
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    # cookie сохранён клиентом → доступ открыт
    r2 = client.get("/actual")
    assert r2.status_code == 200


def test_scope_filters_lots_by_region(client):
    _mk_user("bob", "secret123", regions=["750000000"])
    _mk_lot(1, kato="750000000", category="it", amount=1_000_000)
    _mk_lot(2, kato="710000000", category="it", amount=1_000_000)
    _login(client, "bob", "secret123")
    body = client.get("/actual").text
    assert "Лот 1" in body
    assert "Лот 2" not in body


def test_scope_blocks_out_of_scope_lot_detail(client):
    _mk_user("bob", "secret123", regions=["750000000"])
    _mk_lot(2, kato="710000000", category="it", amount=1_000_000)
    _login(client, "bob", "secret123")
    r = client.get("/lot/2")
    assert r.status_code == 404


def test_admin_sees_all_regions(client):
    _mk_user("root", "secret123", is_admin=True)
    _mk_lot(1, kato="750000000", category="it", amount=1_000_000)
    _mk_lot(2, kato="710000000", category="it", amount=1_000_000)
    _login(client, "root", "secret123")
    body = client.get("/actual").text
    assert "Лот 1" in body and "Лот 2" in body


def test_non_admin_forbidden_on_system_pages(client):
    _mk_user("bob", "secret123")
    _login(client, "bob", "secret123")
    assert client.get("/users").status_code == 403
    assert client.get("/scan").status_code == 403
    assert client.get("/presets").status_code == 403


def test_admin_can_open_users_page(client):
    _mk_user("root", "secret123", is_admin=True)
    _login(client, "root", "secret123")
    assert client.get("/users").status_code == 200


def test_logout_clears_session(client):
    _mk_user("alice", "secret123")
    _login(client, "alice", "secret123")
    assert client.get("/actual").status_code == 200
    client.post("/logout", follow_redirects=False)
    r = client.get("/actual", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
