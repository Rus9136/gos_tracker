"""execute_search с listing_only=True не должен трогать details/docs/LLM.

Глушим `iter_listing` и `fetch_announcement`: проверяем, что:
1) listing-фаза отработала и stat-счётчики проставлены;
2) `fetch_announcement` ни разу не позвали.
"""

from __future__ import annotations

import pytest

from goszakup.db.engine import init_db
from goszakup.db.models import Lot
from goszakup.jobs import run_preset as rp
from goszakup.scraper.search import ListingHit, SearchParams


def _hit(lot_id: int) -> ListingHit:
    return ListingHit(
        lot_id=lot_id,
        lot_number=f"L{lot_id}",
        announcement_id=1000 + lot_id,
        announcement_number=f"A{lot_id}",
        announcement_url=f"https://goszakup.gov.kz/ru/announce/index/{1000 + lot_id}",
        lot_name=f"Тестовый лот {lot_id}",
        customer_name="ТОО Тест",
        enstru="Услуги по сопровождению и технической поддержке информационной системы",
        quantity="1",
        plan_amount=300_000.0,
        amount_raw="300000",
        method="Конкурс",
        status_name="Опубликовано",
    )


@pytest.fixture
def db_session():
    init_db()
    from goszakup.db.engine import SessionLocal

    with SessionLocal() as s:
        yield s


def test_listing_only_skips_phase2(db_session, monkeypatch):
    hits = [_hit(1), _hit(2), _hit(3)]
    monkeypatch.setattr(rp, "iter_listing", lambda params, session=None, **kw: iter(hits))

    fetch_calls = []

    def _fake_fetch(*args, **kwargs):
        fetch_calls.append((args, kwargs))
        raise AssertionError("fetch_announcement не должен вызываться при listing_only=True")

    monkeypatch.setattr(rp, "fetch_announcement", _fake_fetch)

    params = SearchParams(kato="", amount_from=0, status_codes=[])
    stats = rp.execute_search(
        db_session,
        http=None,  # iter_listing мы тоже подменили — http не понадобится
        params=params,
        listing_only=True,
    )

    assert stats.listing_count == 3
    assert stats.new_lots == 3
    assert stats.details_fetched == 0
    assert stats.new_documents == 0
    assert stats.llm_analyzed == 0
    assert not fetch_calls, "phase 2 не должна была запуститься"

    # Лоты-stub'ы реально в БД — это даёт UI возможность их сразу показать.
    db_session.expire_all()
    lots = db_session.query(Lot).all()
    assert {lt.id for lt in lots} >= {1, 2, 3}


def test_default_runs_phase2(db_session, monkeypatch):
    """Sanity: без listing_only детальная фаза действительно идёт."""
    monkeypatch.setattr(rp, "iter_listing", lambda *a, **kw: iter([_hit(10)]))
    fetch_calls = []

    def _fake_fetch(anno_id, session=None):
        fetch_calls.append(anno_id)
        raise RuntimeError("стопаем — нам нужен только сам факт вызова")

    monkeypatch.setattr(rp, "fetch_announcement", _fake_fetch)

    params = SearchParams(kato="", amount_from=0, status_codes=[])
    stats = rp.execute_search(
        db_session, http=None, params=params, listing_only=False
    )

    # Phase 2 стартовала: fetch_announcement позвали. Ошибка внутри отрабатывает
    # через except в execute_search — поэтому stats.errors == 1.
    assert fetch_calls == [1010]
    assert stats.errors >= 1
