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
from goszakup.db.models import Announcement, Lot, UserLotMatch, UserQuery
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


def test_matched_page_renders_deadline_countdown(client, db_session):
    deadline = datetime(2030, 3, 1, 7, 0, tzinfo=UTC)
    db_session.add(Announcement(id=303, url="u/303", application_end=deadline))
    db_session.add(Lot(id=23, url="u/303", announcement_id=303, name="Матч с дедлайном",
                       plan_amount=4_000_000, is_actual=True))
    q = UserQuery(user_id=0, name="Запрос", text="разработка")
    db_session.add(q)
    db_session.flush()
    db_session.add(UserLotMatch(
        user_query_id=q.id, lot_id=23, matched=True, score=80, reason="ok",
        matcher_version="match-v1-gpt-oss-120b", query_version=1,
    ))
    db_session.commit()

    r = client.get("/matched")
    assert r.status_code == 200
    assert "gz-countdown" in r.text
    assert 'data-deadline="2030-03-01T07:00:00+00:00"' in r.text
