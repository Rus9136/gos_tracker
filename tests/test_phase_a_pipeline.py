"""Фаза A SaaS-пивота: храним все лоты, docs/LLM — только watchlist."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis

from goszakup.db.engine import SessionLocal
from goszakup.db.models import Announcement, Lot
from goszakup.jobs.run_preset import _apply_details, execute_search
from goszakup.queue import actors
from goszakup.queue.actors import _normalize_detail_scope
from goszakup.scraper.announce import AnnouncementDetail, LotDetail
from goszakup.scraper.search import ListingHit, SearchParams
from goszakup.sources import API_DEGRADED_KEY


def _hit(lot_id, name, enstru, enstru_code=""):
    return ListingHit(
        lot_id=lot_id,
        lot_number=f"N-{lot_id}",
        announcement_id=lot_id * 100,
        announcement_number=f"A-{lot_id}",
        announcement_url=f"https://goszakup.gov.kz/ru/announce/index/{lot_id * 100}",
        lot_name=name,
        customer_name="Заказчик",
        enstru=enstru,
        quantity="1",
        plan_amount=1_000_000.0,
        amount_raw="1000000",
        method="Запрос ценовых предложений",
        status_name="Опубликовано (прием ценовых предложений)",
        enstru_code=enstru_code,
    )


class _StubSource:
    def __init__(self, hits):
        self.hits = hits

    def iter_listing(self, params, max_pages=None):
        yield from self.hits


HITS = [
    _hit(1, "Компьютер", "Компьютер"),                        # it (keyword-фоллбэк)
    _hit(2, "Препарат", "Лекарственное средство", "211011.200.000000"),  # medicine (код)
    _hit(3, "Стулья офисные", "Стулья офисные"),               # прочее → NULL
]


def test_listing_stores_all_lots_with_verticals(db_session):
    stats = execute_search(
        db_session, _StubSource(HITS), SearchParams(kato="750000000"), listing_only=True
    )
    assert stats.listing_count == 3
    cats = {lot.id: lot.category for lot in db_session.query(Lot).all()}
    assert cats == {1: "it", 2: "medicine", 3: None}


def test_listing_narrows_by_preset_categories(db_session):
    execute_search(
        db_session,
        _StubSource(HITS),
        SearchParams(kato="750000000"),
        categories=["it"],
        listing_only=True,
    )
    assert [lot.id for lot in db_session.query(Lot).all()] == [1]


def test_apply_details_backfills_category_from_code(db_session):
    """Код мог приехать только с деталями (свежие ЗЦП): NULL→значение,
    уже присвоенная вертикаль НЕ перезаписывается чужим кодом."""
    db_session.add(Announcement(id=100, url="u"))
    db_session.flush()
    blank = Lot(id=1, announcement_id=100, url="u", name="Товар", number="L1")
    tagged = Lot(id=2, announcement_id=100, url="u", name="Компьютер", number="L2",
                 category="it")
    db_session.add_all([blank, tagged])
    db_session.flush()

    detail = AnnouncementDetail(
        id=100, url="u",
        lots=[
            LotDetail(
                number="L1", customer_bin="", customer_name="", enstru="", name="",
                extra="", price_per_unit=None, quantity=None, unit="",
                plan_amount=None, amount_y1=None, amount_y2=None, amount_y3=None,
                status_name="", enstru_code="211011.200.000000",
            ),
            LotDetail(
                number="L2", customer_bin="", customer_name="", enstru="", name="",
                extra="", price_per_unit=None, quantity=None, unit="",
                plan_amount=None, amount_y1=None, amount_y2=None, amount_y3=None,
                status_name="", enstru_code="211011.200.000000",
            ),
        ],
    )
    _apply_details(db_session, blank, detail)
    _apply_details(db_session, tagged, detail)
    assert blank.category == "medicine"
    assert tagged.category == "it"  # не перезаписана


def test_normalize_detail_scope_legacy_args():
    # Новые сообщения.
    assert _normalize_detail_scope("all", None) == "all"
    assert _normalize_detail_scope("watchlist", None) == "watchlist"
    # Старый бул позиционно (5-й аргумент попал в detail_scope).
    assert _normalize_detail_scope(True, None) == "watchlist"
    assert _normalize_detail_scope(False, None) == "all"
    # Старый kwarg only_it_lots (ingest/scan).
    assert _normalize_detail_scope("all", False) == "all"
    assert _normalize_detail_scope("all", True) == "watchlist"


def _seed_anno_with_lot(anno_id, category):
    with SessionLocal() as s:
        s.add(Announcement(id=anno_id, url="u"))
        s.flush()
        s.add(Lot(id=anno_id * 10, announcement_id=anno_id, url="u",
                  name="Лот", category=category))
        s.commit()


def test_detail_actor_degrades_to_watchlist_on_api_degraded(db_session, monkeypatch):
    """api_degraded + не-watchlist объявление → скип без fetch_announcement."""
    _seed_anno_with_lot(1, "medicine")
    r = fakeredis.FakeRedis(decode_responses=True)
    r.set(API_DEGRADED_KEY, "test: degraded")
    monkeypatch.setattr(actors, "_redis_client", lambda: r)
    fetch = MagicMock(side_effect=AssertionError("не должен ходить за деталями"))
    monkeypatch.setattr(actors, "make_source", lambda _r: MagicMock(fetch_announcement=fetch))

    actors.detail_actor.fn(1, run_id=0, detail_scope="all")
    fetch.assert_not_called()


def test_detail_actor_skips_docs_without_watchlist_lots(db_session, monkeypatch):
    """detail_scope='all' без деградации: детали тянутся, документы — нет."""
    _seed_anno_with_lot(2, "medicine")
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(actors, "_redis_client", lambda: r)

    detail = AnnouncementDetail(id=2, url="u")
    source = MagicMock()
    source.fetch_announcement.return_value = detail
    monkeypatch.setattr(actors, "make_source", lambda _r: source)
    save_docs = MagicMock(return_value=0)
    monkeypatch.setattr(actors, "_save_documents", save_docs)
    analyze = MagicMock()
    monkeypatch.setattr(actors.analyze_actor, "send", analyze)

    actors.detail_actor.fn(2, run_id=0, with_docs=True, with_llm=True)
    source.fetch_announcement.assert_called_once()
    save_docs.assert_not_called()
    analyze.assert_not_called()
