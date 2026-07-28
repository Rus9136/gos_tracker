"""P0-8: /document/{id}/download обязан применять read-time scope.

Документ показывается на карточке /lot/{id}; значит доступ к нему должен
гейтиться тем же scope, что и сам лот. Иначе любой авторизованный юзер
скачивает ТЗ чужого региона по перебору doc_id, обходя изоляцию.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Announcement, Document, Lot, User
from goszakup.web import auth as auth_mod
from goszakup.web.app import app
from goszakup.web.auth import hash_password

REGION_X = "750000000"
REGION_Y = "710000000"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_mod, "_AUTH_DISABLED", False)
    init_db()
    with SessionLocal() as s:
        s.query(Document).delete()
        s.query(Lot).delete()
        s.query(Announcement).delete()
        s.query(User).delete()
        s.commit()
    with TestClient(app) as c:
        yield c


def _mk_user(username, password, *, is_admin=False, regions=None):
    with SessionLocal() as s:
        u = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
            is_active=True,
            regions=regions,
        )
        s.add(u)
        s.commit()
        return u.id


def _mk_doc_in_region(tmp_path: Path, *, anno_id, doc_id, kato) -> str:
    """Announcement + один лот в регионе `kato` + документ на диске."""
    f = tmp_path / f"tz_{doc_id}.pdf"
    f.write_bytes(b"%PDF-1.4 fake tz")
    with SessionLocal() as s:
        s.add(Announcement(id=anno_id, url=f"https://goszakup.gov.kz/ru/announce/index/{anno_id}"))
        s.add(
            Lot(
                id=anno_id * 10 + 1,
                announcement_id=anno_id,
                url="https://goszakup.gov.kz/x",
                name=f"Лот в {kato}",
                kato=kato,
                category="it",
                plan_amount=Decimal(1_000_000),
                status_code=210,
                is_actual=True,
            )
        )
        s.add(
            Document(
                id=doc_id,
                announcement_id=anno_id,
                name="Техническая спецификация.pdf",
                url=f"https://v3bl.goszakup.gov.kz/f/{doc_id}",
                local_path=str(f),
                content_type="application/pdf",
            )
        )
        s.commit()
    return str(f)


def _login(client, username, password):
    return client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )


def test_out_of_scope_document_is_404(client, tmp_path):
    _mk_doc_in_region(tmp_path, anno_id=102, doc_id=500, kato=REGION_Y)
    _mk_user("bob", "secret123", regions=[REGION_X])
    _login(client, "bob", "secret123")
    r = client.get("/document/500/download")
    assert r.status_code == 404


def test_in_scope_owner_gets_document(client, tmp_path):
    _mk_doc_in_region(tmp_path, anno_id=102, doc_id=500, kato=REGION_Y)
    _mk_user("carol", "secret123", regions=[REGION_Y])
    _login(client, "carol", "secret123")
    r = client.get("/document/500/download")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 fake tz"


def test_admin_gets_any_document(client, tmp_path):
    _mk_doc_in_region(tmp_path, anno_id=102, doc_id=500, kato=REGION_Y)
    _mk_user("root", "secret123", is_admin=True)
    _login(client, "root", "secret123")
    r = client.get("/document/500/download")
    assert r.status_code == 200
