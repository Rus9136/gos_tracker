"""Отбор кандидатов в `cli reanalyze`.

Команда ходит в LLM за деньги, поэтому важно не «сколько лотов она осилит»,
а какие вообще попадают в выборку: исторический watchlist на порядок больше
актуального (08.2026 — 9.5k против сотни), и после бампа ANALYZER_VERSION
прогон по умолчанию не должен перемалывать давно закрытые лоты.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from goszakup import cli as cli_mod
from goszakup.db.models import Announcement, Lot, User


@pytest.fixture
def seeded(db_session, monkeypatch):
    db_session.execute(User.__table__.delete())
    db_session.add(User(username="u", password_hash="", is_admin=False, categories=["it"]))
    db_session.add(Announcement(id=1, url="https://goszakup.gov.kz/ru/announce/index/1"))
    db_session.add_all([
        Lot(id=1, url="https://goszakup.gov.kz/ru/announce/index/1", announcement_id=1,
            name="актуальный", category="it", is_actual=True),
        Lot(id=2, url="https://goszakup.gov.kz/ru/announce/index/1", announcement_id=1,
            name="закрытый", category="it", is_actual=False),
    ])
    db_session.commit()

    from goszakup.watchlist import invalidate_watchlist_cache

    invalidate_watchlist_cache()
    # cli открывает собственную SessionLocal — она смотрит в ту же tmp-БД.
    return db_session


def _run(monkeypatch, *args):
    seen: list[int] = []
    monkeypatch.setattr(cli_mod, "analyze_and_save", lambda s, lot: seen.append(lot.id) or False)
    result = CliRunner().invoke(cli_mod.app, ["reanalyze", *args])
    assert result.exit_code == 0, result.output
    return seen


def test_past_lots_are_skipped_by_default(seeded, monkeypatch):
    assert _run(monkeypatch) == [1]


def test_include_past_widens_selection(seeded, monkeypatch):
    assert sorted(_run(monkeypatch, "--include-past")) == [1, 2]


def test_lot_id_ignores_actuality(seeded, monkeypatch):
    # Точечный запуск по закрытому лоту обязан работать — иначе разобрать
    # конкретную жалобу «почему этот лот без анализа» было бы нечем.
    assert _run(monkeypatch, "--lot-id", "2") == [2]
