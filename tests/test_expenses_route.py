"""Smoke /expenses + учёт LlmCall.

GZ_NO_AUTH=1 (conftest) → синтетический dev-админ, страница доступна.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from goszakup.classify.usage import record_call, usage_from_response
from goszakup.db.engine import init_db
from goszakup.db.models import LlmCall
from goszakup.web.app import app


@pytest.fixture
def client(db_session):
    init_db()
    db_session.execute(LlmCall.__table__.delete())
    db_session.commit()
    with TestClient(app) as c:
        yield c


class _FakeUsage:
    prompt_tokens = 1200
    completion_tokens = 300
    total_tokens = 1500


class _FakeResp:
    usage = _FakeUsage()


def test_usage_from_response_extracts_tokens():
    u = usage_from_response(_FakeResp(), "gpt-oss-120b")
    assert u == {
        "model": "gpt-oss-120b",
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
    }


def test_usage_from_response_none_when_no_usage():
    assert usage_from_response(object(), "m") is None


def test_record_call_writes_row_and_skips_none(db_session):
    record_call(db_session, "analyze", None, lot_id=5)  # no-op
    assert db_session.query(LlmCall).count() == 0

    record_call(
        db_session,
        "match",
        {"model": "m", "prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        lot_id=7,
        user_id=0,  # dev-аноним → пишется как NULL
    )
    row = db_session.query(LlmCall).one()
    assert row.kind == "match"
    assert row.total_tokens == 14
    assert row.lot_id == 7
    assert row.user_id is None


def test_expenses_page_aggregates(client, db_session):
    now = datetime.now(UTC)
    db_session.add_all(
        [
            LlmCall(
                kind="analyze", model="m", prompt_tokens=1000,
                completion_tokens=200, total_tokens=1200, created_at=now,
            ),
            LlmCall(
                kind="match", model="m", prompt_tokens=500,
                completion_tokens=50, total_tokens=550,
                created_at=now - timedelta(days=2),
            ),
        ]
    )
    db_session.commit()

    r = client.get("/expenses")
    assert r.status_code == 200
    assert "Расход токенов" in r.text
    # Суммарные токены 1200+550 = 1750 → форматируется как «1 750».
    assert "1 750" in r.text
    assert "Анализ ТЗ" in r.text
    assert "Подбор" in r.text
