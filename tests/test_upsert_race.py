"""Гейт 2 (P0 №4): гонка insert'а лота не рушит листинг-проход.

Два прогона (daily vs /scan) с пересекающимися kato могут одновременно вставлять
один лот. На Postgres второй INSERT → IntegrityError; без изоляции он откатывал
бы весь Phase-1 листинг. Проверяем, что _upsert_lot_from_listing переживает
конфликт через savepoint и продолжает как update (детерминированно на SQLite:
форсируем промах начального get при уже существующем ряде).
"""

from __future__ import annotations

from goszakup.db.models import Announcement, Lot
from goszakup.jobs import run_preset
from goszakup.scraper.search import ListingHit


def _hit(lot_id=5, name="новое имя"):
    return ListingHit(
        lot_id=lot_id,
        lot_number="1",
        announcement_id=100,
        announcement_number="A-100",
        announcement_url="https://goszakup.gov.kz/ru/announce/index/100",
        lot_name=name,
        customer_name="Орг",
        enstru="620000",
        quantity="1",
        plan_amount=1_000_000.0,
        amount_raw="1 000 000",
        method="открытый",
        status_name="Опубликовано (прием заявок)",
    )


def test_insert_race_falls_back_to_update(db_session, monkeypatch):
    # Лот уже в БД (его вставил параллельный прогон).
    db_session.add(Announcement(id=100, url="u/100"))
    db_session.add(Lot(id=5, announcement_id=100, url="u/100", name="старое имя"))
    db_session.commit()

    # Но на момент проверки _get_or_insert_lot его «не видит» (гонка): форсируем
    # первый get(Lot,5)→None, дальше — реальный get.
    real_get = db_session.get
    state = {"missed": False}

    def fake_get(cls, ident, *a, **k):
        if cls is Lot and ident == 5 and not state["missed"]:
            state["missed"] = True
            return None
        return real_get(cls, ident, *a, **k)

    monkeypatch.setattr(db_session, "get", fake_get)

    on_new: list = []
    on_change: list = []
    # НЕ должно бросить IntegrityError.
    lot = run_preset._upsert_lot_from_listing(
        db_session, _hit(), kato="750000000", on_new=on_new, on_status_change=on_change
    )
    db_session.commit()

    assert lot.id == 5
    assert on_new == []  # распознали существующий — не «новый»
    assert db_session.query(Lot).filter_by(id=5).count() == 1  # не задвоился
    assert db_session.get(Lot, 5).name == "новое имя"  # апдейт применился


def test_normal_insert_creates_lot(db_session):
    db_session.add(Announcement(id=100, url="u/100"))
    db_session.commit()

    on_new: list = []
    lot = run_preset._upsert_lot_from_listing(
        db_session, _hit(lot_id=7), kato="750000000", on_new=on_new, on_status_change=[]
    )
    db_session.commit()

    assert lot.id == 7
    assert len(on_new) == 1
    assert db_session.query(Lot).filter_by(id=7).count() == 1
