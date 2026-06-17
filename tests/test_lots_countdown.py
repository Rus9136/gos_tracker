"""Smoke: колонка обратного отсчёта до окончания приёма заявок в списке лотов.

GZ_NO_AUTH=1 (conftest) → dev-админ видит всё. Проверяем, что дедлайн из
Announcement.application_end попадает в data-атрибут (ISO с tz), а лот без
дедлайна не падает.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import init_db
from goszakup.db.models import Announcement, Lot
from goszakup.web.app import app


@pytest.fixture
def client(db_session):
    init_db()
    with TestClient(app) as c:
        yield c


def test_actual_list_renders_deadline_countdown(client, db_session):
    deadline = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    db_session.add(Announcement(id=301, url="u/301", application_end=deadline))
    db_session.add(Lot(id=21, url="u/301", announcement_id=301, name="Лот с дедлайном",
                       plan_amount=2_000_000, is_actual=True))
    db_session.commit()

    r = client.get("/actual")
    assert r.status_code == 200
    assert "gz-countdown" in r.text
    # ISO-метка с tz-смещением — JS парсит её как UTC.
    assert 'data-deadline="2030-01-01T09:00:00+00:00"' in r.text


def test_actual_list_without_deadline_renders_dash(client, db_session):
    db_session.add(Announcement(id=302, url="u/302"))  # application_end = NULL
    db_session.add(Lot(id=22, url="u/302", announcement_id=302, name="Лот без дедлайна",
                       plan_amount=1_000_000, is_actual=True))
    db_session.commit()

    r = client.get("/actual")
    assert r.status_code == 200
    assert "Лот без дедлайна" in r.text
    # Без дедлайна data-атрибут не появляется для этого лота — таймера нет.
    assert 'data-deadline=""' not in r.text
