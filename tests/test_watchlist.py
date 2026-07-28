"""Watchlist-заглушка фазы A: дорогие стадии только для вертикали IT."""

from goszakup.classify import llm as llm_mod
from goszakup.db.models import Announcement, Lot
from goszakup.watchlist import should_analyze


def _mk_lot(db_session, lot_id, category):
    ann = Announcement(id=lot_id * 10, url="https://example/a")
    db_session.add(ann)
    db_session.flush()
    lot = Lot(
        id=lot_id,
        announcement_id=ann.id,
        url="https://example/lot",
        name="Лот",
        category=category,
    )
    db_session.add(lot)
    db_session.flush()
    return lot


def test_should_analyze_stub(db_session):
    assert should_analyze(db_session, _mk_lot(db_session, 1, "it"))
    assert not should_analyze(db_session, _mk_lot(db_session, 2, "medicine"))
    assert not should_analyze(db_session, _mk_lot(db_session, 3, None))


def test_analyze_inner_refuses_non_watchlist(db_session, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("LLM не должен вызываться вне watchlist")

    monkeypatch.setattr(llm_mod, "_call_llm", _boom)
    lot = _mk_lot(db_session, 4, "medicine")
    assert llm_mod.analyze_and_save(db_session, lot) is False
