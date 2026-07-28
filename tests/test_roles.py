"""Роли UI: видимость вкладок по роли, CRUD /roles, назначение в /users.

Как в test_auth_users.py — conftest ставит GZ_NO_AUTH=1, поэтому реальную
аутентификацию включаем monkeypatch'ем `_AUTH_DISABLED=False`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Lot, Role, User
from goszakup.web import auth as auth_mod
from goszakup.web.app import app
from goszakup.web.auth import hash_password


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_mod, "_AUTH_DISABLED", False)
    init_db()
    with SessionLocal() as s:
        # Lot тоже чистим (как в test_auth_users): лоты с NULL plan_amount от
        # соседних тестов роняют sum() в lots.html на страницах /actual, /past.
        s.query(Lot).delete()
        s.query(User).delete()
        s.query(Role).delete()
        s.commit()
    with TestClient(app) as c:
        yield c


def _mk_role(name, pages):
    with SessionLocal() as s:
        r = Role(name=name, pages=pages)
        s.add(r)
        s.commit()
        return r.id


def _mk_user(username, password, *, is_admin=False, role_id=None):
    with SessionLocal() as s:
        u = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
            is_active=True,
            role_id=role_id,
        )
        s.add(u)
        s.commit()
        return u.id


def _login(client, username, password):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_user_without_role_sees_all_user_pages(client):
    _mk_user("bob", "secret123")
    _login(client, "bob", "secret123")
    for path in ("/", "/actual", "/past", "/starred", "/organizations",
                 "/matched", "/queries", "/settings"):
        assert client.get(path).status_code == 200, path


def test_role_restricts_pages(client):
    rid = _mk_role("Только актуальные", ["actual", "settings"])
    _mk_user("bob", "secret123", role_id=rid)
    _login(client, "bob", "secret123")
    assert client.get("/actual").status_code == 200
    assert client.get("/settings").status_code == 200
    assert client.get("/past").status_code == 403
    assert client.get("/matched").status_code == 403
    assert client.get("/queries").status_code == 403
    assert client.get("/organizations").status_code == 403


def test_role_hides_tabs_in_sidebar(client):
    rid = _mk_role("Только актуальные", ["actual"])
    _mk_user("bob", "secret123", role_id=rid)
    _login(client, "bob", "secret123")
    body = client.get("/actual").text
    assert 'href="/actual"' in body
    assert 'href="/past"' not in body
    assert 'href="/queries"' not in body


def test_dashboard_redirects_to_first_allowed(client):
    rid = _mk_role("Без дашборда", ["past", "actual"])
    _mk_user("bob", "secret123", role_id=rid)
    _login(client, "bob", "secret123")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    # порядок реестра PAGES: actual раньше past
    assert r.headers["location"] == "/actual"


def test_empty_role_gets_403_everywhere(client):
    rid = _mk_role("Пустая", [])
    _mk_user("bob", "secret123", role_id=rid)
    _login(client, "bob", "secret123")
    assert client.get("/", follow_redirects=False).status_code == 403
    assert client.get("/actual").status_code == 403


def test_admin_ignores_role(client):
    rid = _mk_role("Только актуальные", ["actual"])
    _mk_user("root", "secret123", is_admin=True, role_id=rid)
    _login(client, "root", "secret123")
    assert client.get("/past").status_code == 200
    assert client.get("/queries").status_code == 200


def test_role_gates_queries_posts_too(client):
    rid = _mk_role("Без запросов", ["actual"])
    _mk_user("bob", "secret123", role_id=rid)
    _login(client, "bob", "secret123")
    r = client.post("/queries", data={"name": "n", "text": "t"})
    assert r.status_code == 403


def test_roles_page_admin_only(client):
    _mk_user("bob", "secret123")
    _mk_user("root", "secret123", is_admin=True)
    _login(client, "bob", "secret123")
    assert client.get("/roles").status_code == 403
    client.post("/logout")
    _login(client, "root", "secret123")
    assert client.get("/roles").status_code == 200


def test_roles_crud_flow(client):
    _mk_user("root", "secret123", is_admin=True)
    _login(client, "root", "secret123")

    r = client.post(
        "/roles",
        data={"name": "Менеджер", "pages": ["actual", "past", "bogus"]},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/roles?ok=created"
    with SessionLocal() as s:
        role = s.query(Role).filter_by(name="Менеджер").one()
        assert role.pages == ["actual", "past"]  # bogus отфильтрован
        rid = role.id

    r = client.post(
        f"/roles/{rid}/edit",
        data={"name": "Менеджер", "pages": ["actual"]},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/roles?ok=saved"
    with SessionLocal() as s:
        assert s.get(Role, rid).pages == ["actual"]

    # дубликат имени
    client.post("/roles", data={"name": "Вторая", "pages": []})
    r = client.post(
        f"/roles/{rid}/edit", data={"name": "Вторая", "pages": []},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/roles?error=exists"

    r = client.post(f"/roles/{rid}/delete", follow_redirects=False)
    assert r.headers["location"] == "/roles?ok=deleted"
    with SessionLocal() as s:
        assert s.get(Role, rid) is None


def test_role_delete_blocked_while_assigned(client):
    rid = _mk_role("Занятая", ["actual"])
    _mk_user("bob", "secret123", role_id=rid)
    _mk_user("root", "secret123", is_admin=True)
    _login(client, "root", "secret123")
    r = client.post(f"/roles/{rid}/delete", follow_redirects=False)
    assert r.headers["location"] == "/roles?error=in_use"
    with SessionLocal() as s:
        assert s.get(Role, rid) is not None


def test_assign_role_via_users_edit(client):
    rid = _mk_role("Менеджер", ["actual"])
    uid = _mk_user("bob", "secret123")
    _mk_user("root", "secret123", is_admin=True)
    _login(client, "root", "secret123")

    r = client.post(
        f"/users/{uid}/edit",
        data={"is_active": "1", "role_id": str(rid)},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/users?ok=saved"
    with SessionLocal() as s:
        assert s.get(User, uid).role_id == rid

    # снять роль пустым значением
    client.post(f"/users/{uid}/edit", data={"is_active": "1", "role_id": ""})
    with SessionLocal() as s:
        assert s.get(User, uid).role_id is None
