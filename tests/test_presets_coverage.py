"""Страница /presets — покрытие ежедневного сбора.

После перехода на API от preset'ов в daily остаются только статусы и
минимальная сумма (jobs/incremental.daily_scan_params), поэтому форма
пишет их сразу во все preset'ы — включая выключенные, иначе включённый
позже регион принёс бы в HTML-фолбэк устаревший набор.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Preset
from goszakup.web.app import app


@pytest.fixture
def client():
    init_db()
    with SessionLocal() as s:
        s.query(Preset).delete()
        s.add_all(
            [
                Preset(
                    name="Алматы — актуальные",
                    kato="750000000",
                    amount_from=500_000,
                    status_codes=[210, 220],
                    active=True,
                ),
                Preset(
                    name="Астана — актуальные",
                    kato="710000000",
                    amount_from=700_000,
                    status_codes=[230],
                    active=False,
                ),
            ]
        )
        s.commit()
    with TestClient(app) as c:
        yield c


def test_form_shows_current_coverage(client):
    r = client.get("/presets")
    assert r.status_code == 200
    body = " ".join(r.text.split())
    assert 'name="amount_from"' in body and 'name="status"' in body
    # daily_scan_params: min(amount_from) активных = 500 000
    assert 'value="500000"' in body
    # Отмечены статусы активного preset'а, статус выключенного — нет.
    assert 'name="status" value="210" checked' in body
    assert 'name="status" value="230" checked' not in body


def test_save_applies_to_all_presets_including_inactive(client):
    r = client.post(
        "/presets/coverage",
        data={"status": ["210", "240"], "amount_from": "1000000"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/presets?saved=1"

    with SessionLocal() as s:
        for p in s.query(Preset).all():
            assert p.status_codes == [210, 240]
            assert p.amount_from == 1_000_000


def test_empty_statuses_rejected(client):
    r = client.post(
        "/presets/coverage",
        data={"amount_from": "1000000"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/presets?error=no_statuses"

    with SessionLocal() as s:
        # Ничего не записано — иначе daily остался бы без статусов.
        assert s.query(Preset).filter(Preset.amount_from == 1_000_000).count() == 0


def test_toggle_still_works(client):
    with SessionLocal() as s:
        pid = s.query(Preset).filter(Preset.active.is_(False)).one().id
    r = client.post(f"/presets/{pid}/toggle", follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as s:
        assert s.get(Preset, pid).active is True
