"""Плашка «идёт прогон» на отчёте организации.

`find_active_run` глобален: он видит ЛЮБОЙ живой прогон, включая ежедневные
синки (bids/plans/api-daily), которые к отчёту отношения не имеют. Раньше
любой из них рисовал на странице предупреждение «отчёт пересчитается по
свежим данным» — оно висело часами и вводило в заблуждение. Теперь громкая
плашка только про дозагрузку по БИН этой организации; чужой прогон лишь
дизейблит кнопку (create_ingest_run всё равно откажет — Crawl-delay один).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Organization, ScrapeRun
from goszakup.web.app import app

BIN = "123456789012"


@pytest.fixture
def client():
    init_db()
    with SessionLocal() as s:
        s.query(ScrapeRun).delete()
        org = s.query(Organization).filter(Organization.bin == BIN).one_or_none()
        if org is None:
            org = Organization(name="ТОО Тест", bin=BIN)
            s.add(org)
        s.commit()
        org_id = org.id
    with TestClient(app) as c:
        c.org_id = org_id  # type: ignore[attr-defined]
        yield c


def _add_run(note: str) -> None:
    with SessionLocal() as s:
        s.add(ScrapeRun(preset_id=None, note=note))
        s.commit()


def test_foreign_sync_does_not_alarm(client):
    _add_run("bids-sync: горизонт 45д, лимит 500")
    body = client.get(f"/organization/{client.org_id}/report").text
    assert "отчёт пересчитается" not in body
    # но кнопку блокируем и объясняем, чем занято
    assert "disabled" in body
    assert "занято прогоном" in body


def test_own_ingest_run_shows_banner(client):
    _add_run(f"БИН {BIN} 2024–2026 услуги")
    body = client.get(f"/organization/{client.org_id}/report").text
    assert "Идёт дозагрузка по этой организации" in body
    assert "отчёт пересчитается" in body


def test_no_runs_no_banner(client):
    body = client.get(f"/organization/{client.org_id}/report").text
    assert "отчёт пересчитается" not in body
    assert "занято прогоном" not in body
