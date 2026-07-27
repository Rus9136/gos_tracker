"""execute_search с listing_only=True не должен трогать details/docs/LLM.

Подсовываем фейковый DataSource: проверяем, что:
1) listing-фаза отработала и stat-счётчики проставлены;
2) `fetch_announcement` источника ни разу не позвали.
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


class _FakeSource:
    def __init__(self, hits):
        self.hits = hits
        self.fetch_calls: list[int] = []

    def iter_listing(self, params, max_pages=None):
        return iter(self.hits)

    def fetch_announcement(self, anno_id):
        self.fetch_calls.append(anno_id)
        raise RuntimeError("стопаем — нам нужен только сам факт вызова")

    def fetch_modal_files(self, anno_id, file_type_id):
        return []

    def fetch_enstru_code(self, trd_buy_id, lot_id):
        return None

    def download(self, anno_id, url, suggested_name=None):
        raise AssertionError("download не должен вызываться в этих тестах")


@pytest.fixture
def db_session():
    init_db()
    from goszakup.db.engine import SessionLocal

    with SessionLocal() as s:
        yield s


def test_listing_only_skips_phase2(db_session):
    source = _FakeSource([_hit(1), _hit(2), _hit(3)])

    params = SearchParams(kato="", amount_from=0, status_codes=[])
    stats = rp.execute_search(db_session, source, params=params, listing_only=True)

    assert stats.listing_count == 3
    assert stats.new_lots == 3
    assert stats.details_fetched == 0
    assert stats.new_documents == 0
    assert stats.llm_analyzed == 0
    assert not source.fetch_calls, "phase 2 не должна была запуститься"

    # Лоты-stub'ы реально в БД — это даёт UI возможность их сразу показать.
    db_session.expire_all()
    lots = db_session.query(Lot).all()
    assert {lt.id for lt in lots} >= {1, 2, 3}


def test_empty_kato_does_not_clobber_region(db_session):
    """Общереспубликанский проход (api-daily, /scan «весь РК») идёт с kato=''
    — он не должен затирать регион, от которого зависит persona-scope."""
    from goszakup.db.models import Lot

    source = _FakeSource([_hit(50)])
    rp.execute_search(
        db_session, source, params=SearchParams(kato="790000000", amount_from=0),
        listing_only=True,
    )
    source2 = _FakeSource([_hit(50)])
    rp.execute_search(
        db_session, source2, params=SearchParams(kato="", amount_from=0),
        listing_only=True,
    )
    db_session.expire_all()
    assert db_session.get(Lot, 50).kato == "790000000"


def test_default_runs_phase2(db_session):
    """Sanity: без listing_only детальная фаза действительно идёт."""
    source = _FakeSource([_hit(10)])

    params = SearchParams(kato="", amount_from=0, status_codes=[])
    stats = rp.execute_search(db_session, source, params=params, listing_only=False)

    # Phase 2 стартовала: fetch_announcement позвали. Ошибка внутри отрабатывает
    # через except в execute_search — поэтому stats.errors == 1.
    assert source.fetch_calls == [1010]
    assert stats.errors >= 1
