"""Smoke /scan и /scan/run.

Проверяем рендер формы, успешный POST и пару ошибочных кейсов. Реальный
`scan_actor.send` подменяется monkeypatch'ем — нет нужды поднимать Redis.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import ScrapeRun
from goszakup.queue import actors as actors_mod
from goszakup.web.app import app


@pytest.fixture
def client(monkeypatch):
    init_db()
    # Очищаем активные run'ы между тестами, чтобы find_active_run не блокировал.
    with SessionLocal() as s:
        s.query(ScrapeRun).delete()
        s.commit()
    sent: list[tuple] = []
    monkeypatch.setattr(
        actors_mod.scan_actor, "send", lambda *args, **kwargs: sent.append((args, kwargs))
    )
    with TestClient(app) as c:
        c.scan_sent = sent  # type: ignore[attr-defined]
        yield c


def test_scan_form_renders(client):
    r = client.get("/scan")
    assert r.status_code == 200
    body = r.text
    # ключевые поля формы
    assert 'name="kato"' in body
    assert 'name="amount_from"' in body
    assert 'name="amount_to"' in body
    assert 'name="status"' in body
    assert 'name="category"' in body
    assert 'name="mode"' in body
    # все 20 регионов в селекте + «Весь РК»
    assert "Весь РК" in body
    assert "Алматы" in body and "Шымкент" in body


def test_scan_run_creates_scraperun_and_enqueues(client):
    r = client.post(
        "/scan/run",
        data={
            "kato": "750000000",
            "amount_from": "300000",
            "amount_to": "1000000",
            "status": ["210", "220"],
            "category": ["it"],
            "mode": "listing",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/runs/")

    # ScrapeRun создан, preset_id=NULL, note осмысленный
    with SessionLocal() as s:
        run = s.query(ScrapeRun).order_by(ScrapeRun.id.desc()).first()
        assert run is not None
        assert run.preset_id is None
        assert run.note is not None
        assert "Алматы" in run.note
        assert "только листинг" in run.note

    # scan_actor.send позван ровно один раз с правильными аргументами
    sent = client.scan_sent  # type: ignore[attr-defined]
    assert len(sent) == 1
    args, _ = sent[0]
    run_id, kato, amount_from, amount_to, statuses, its, listing_only, with_docs, with_llm = args
    assert kato == "750000000"
    assert amount_from == 300000
    assert amount_to == 1000000
    assert statuses == [210, 220]
    assert its == ["it"]
    assert listing_only is True
    assert with_docs is False
    assert with_llm is False


def test_scan_run_full_mode_flags(client):
    r = client.post(
        "/scan/run",
        data={"kato": "", "amount_from": "500000", "mode": "full"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    sent = client.scan_sent  # type: ignore[attr-defined]
    args, _ = sent[-1]
    _, kato, _, _, _, _, listing_only, with_docs, with_llm = args
    assert kato == ""
    assert listing_only is False
    assert with_docs is True
    assert with_llm is True


def test_scan_run_invalid_amount_range(client):
    r = client.post(
        "/scan/run",
        data={"amount_from": "1000000", "amount_to": "500000", "mode": "full"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/scan?error=amount_range"


def test_scan_run_invalid_kato(client):
    r = client.post(
        "/scan/run",
        data={"kato": "999000000", "mode": "full"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/scan?error=kato_invalid"


def test_scan_run_busy(client):
    """Если активный run уже есть — отдаём ошибку busy и не шлём в очередь."""
    with SessionLocal() as s:
        s.add(ScrapeRun(preset_id=None, note="фейковый активный run"))
        s.commit()

    sent_before = len(client.scan_sent)  # type: ignore[attr-defined]
    r = client.post(
        "/scan/run",
        data={"amount_from": "0", "mode": "full"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/scan?error=busy"
    assert len(client.scan_sent) == sent_before  # type: ignore[attr-defined]
