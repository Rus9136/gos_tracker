"""Плашка «идёт прогон» на отчёте организации.

`find_active_run` глобален: он видит ЛЮБОЙ живой прогон, включая ежедневные
синки (bids/plans/api-daily), которые к отчёту отношения не имеют. Раньше
любой из них рисовал на странице предупреждение про пересчёт отчёта — оно
висело часами и вводило в заблуждение. Теперь громкая
плашка только про дозагрузку по БИН этой организации; чужой прогон лишь
дизейблит кнопку (create_ingest_run всё равно откажет — Crawl-delay один).
"""

from __future__ import annotations

from datetime import UTC, datetime

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
    assert "Идёт дозагрузка по этой организации" not in body
    # но кнопку блокируем и объясняем, чем занято
    assert "disabled" in body
    assert "занято прогоном" in body


def test_own_ingest_run_shows_banner(client):
    _add_run(f"БИН {BIN} 2024–2026 услуги")
    body = client.get(f"/organization/{client.org_id}/report").text
    assert "Идёт дозагрузка по этой организации" in body
    assert "Страница обновится сама" in body


def test_no_runs_no_banner(client):
    body = client.get(f"/organization/{client.org_id}/report").text
    assert "Идёт дозагрузка по этой организации" not in body
    assert "занято прогоном" not in body
    assert 'id="run-progress"' not in body


def test_banner_carries_progress_indicator(client):
    _add_run(f"БИН {BIN} 2024–2026 услуги")
    body = client.get(f"/organization/{client.org_id}/report").text
    assert 'id="run-progress"' in body
    assert "/static/js/progress.js" in body


def test_progress_endpoint_reports_phases(client):
    _add_run(f"БИН {BIN} 2024–2026 услуги")
    with SessionLocal() as s:
        run = s.query(ScrapeRun).order_by(ScrapeRun.id.desc()).first()
        run.listing_count = 42
        s.commit()
        run_id = run.id

    # Redis в тестах нет — фаза неопределённая, но эндпоинт обязан отвечать.
    p = client.get(f"/runs/{run_id}/progress").json()
    assert p["finished"] is False
    assert p["phase"] == "listing"
    assert p["listing_count"] == 42

    with SessionLocal() as s:
        s.get(ScrapeRun, run_id).finished_at = datetime.now(UTC)
        s.commit()
    assert client.get(f"/runs/{run_id}/progress").json() == {
        "finished": True, "phase": "done", "percent": 100
    }


def test_progress_of_missing_run_is_404(client):
    assert client.get("/runs/999999/progress").status_code == 404
