"""Идемпотентность и upsert match_and_save — со стаб-LLM, без сети.

Зеркалит контракт LotAnalysis: пересчёт пропускается при совпадении
(query_version, matcher_version); правка запроса (version++) пересчитывает
через upsert без дублей; ошибка LLM не оставляет записи.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from goszakup.classify import matcher
from goszakup.classify.matcher import MATCHER_VERSION, MatchResult, _Outcome, match_and_save
from goszakup.db.models import Announcement, Lot, LotAnalysis, User, UserLotMatch, UserQuery


@pytest.fixture
def pair(db_session):
    db_session.execute(UserLotMatch.__table__.delete())
    db_session.execute(UserQuery.__table__.delete())
    db_session.execute(User.__table__.delete())
    user = User(username="u1", password_hash="", is_admin=False, is_active=True)
    db_session.add(user)
    db_session.flush()
    query = UserQuery(user_id=user.id, name="1С", text="разработка 1С")
    db_session.add(query)
    db_session.add(Announcement(id=101, url="u/101"))
    lot = Lot(id=1, url="u/101", announcement_id=101, name="Доработка 1С")
    db_session.add(lot)
    db_session.flush()
    return query, lot


class _StubLLM:
    def __init__(self, result: MatchResult | None, error: str | None = None):
        self.result, self.error, self.calls = result, error, 0

    def __call__(self, query_text, lot):
        self.calls += 1
        return _Outcome(self.result, self.error)


def test_first_match_saved_then_idempotent(db_session, pair, monkeypatch):
    query, lot = pair
    stub = _StubLLM(MatchResult(matched=True, score=85, reason="подходит"))
    monkeypatch.setattr(matcher, "_call_llm", stub)

    assert match_and_save(db_session, query, lot) is True
    row = db_session.query(UserLotMatch).one()
    assert row.matched is True and row.score == 85
    assert row.matcher_version == MATCHER_VERSION
    assert row.query_version == query.version

    # Повтор — без LLM-вызова.
    assert match_and_save(db_session, query, lot) is False
    assert stub.calls == 1


def test_version_bump_recomputes_via_upsert(db_session, pair, monkeypatch):
    query, lot = pair
    stub = _StubLLM(MatchResult(matched=True, score=70, reason="ок"))
    monkeypatch.setattr(matcher, "_call_llm", stub)
    assert match_and_save(db_session, query, lot) is True

    query.version += 1
    db_session.flush()
    stub.result = MatchResult(matched=False, score=20, reason="уже не подходит")

    assert match_and_save(db_session, query, lot) is True
    assert stub.calls == 2
    rows = db_session.query(UserLotMatch).all()
    assert len(rows) == 1  # upsert, не дубль
    assert rows[0].matched is False and rows[0].score == 20
    assert rows[0].query_version == query.version


def test_matcher_version_mismatch_recomputes(db_session, pair, monkeypatch):
    query, lot = pair
    stub = _StubLLM(MatchResult(matched=True, score=90, reason="ок"))
    monkeypatch.setattr(matcher, "_call_llm", stub)
    assert match_and_save(db_session, query, lot) is True

    db_session.query(UserLotMatch).one().matcher_version = "match-v0-old"
    db_session.flush()

    assert match_and_save(db_session, query, lot) is True
    assert stub.calls == 2
    assert db_session.query(UserLotMatch).one().matcher_version == MATCHER_VERSION


def test_reanalyzed_lot_recomputes_match(db_session, pair, monkeypatch):
    """Переанализ лота (свежий analyzed_at) инвалидирует матч, даже если
    query_version и matcher_version не менялись."""
    query, lot = pair
    db_session.add(
        LotAnalysis(lot_id=lot.id, analyzer_version="v", tz_summary="старое ТЗ")
    )
    db_session.flush()
    db_session.refresh(lot)

    stub = _StubLLM(MatchResult(matched=True, score=80, reason="ок"))
    monkeypatch.setattr(matcher, "_call_llm", stub)
    assert match_and_save(db_session, query, lot) is True
    assert match_and_save(db_session, query, lot) is False  # анализ не менялся

    lot.analysis.analyzed_at = datetime.now(UTC) + timedelta(seconds=1)
    db_session.flush()

    assert match_and_save(db_session, query, lot) is True
    assert stub.calls == 2
    assert db_session.query(UserLotMatch).count() == 1


def test_llm_error_leaves_no_row(db_session, pair, monkeypatch):
    query, lot = pair
    stub = _StubLLM(None, error="CEREBRAS_API_KEY не задан")
    monkeypatch.setattr(matcher, "_call_llm", stub)

    assert match_and_save(db_session, query, lot) is False
    assert db_session.query(UserLotMatch).count() == 0


def test_crash_inside_does_not_propagate(db_session, pair, monkeypatch):
    query, lot = pair

    def boom(*a, **k):
        raise RuntimeError("сеть упала")

    monkeypatch.setattr(matcher, "_call_llm", boom)
    assert match_and_save(db_session, query, lot) is False
    assert db_session.query(UserLotMatch).count() == 0
