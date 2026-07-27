"""Синк договоров из API: upsert, победители, фильтрация чужих лотов."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Announcement, Contract, Lot
from goszakup.jobs.contracts import (
    apply_winner_from_contract,
    sync_contracts,
    upsert_contract_from_api,
)

FIXTURES = Path(__file__).parent / "fixtures" / "api"


def _contracts_fixture():
    return json.loads((FIXTURES / "contracts_listing.json").read_text())


@pytest.fixture
def session():
    init_db()
    with SessionLocal() as s:
        s.query(Contract).delete()
        s.query(Lot).delete()
        s.query(Announcement).delete()
        s.commit()
        yield s


def _make_lot(session, lot_id: int, anno_id: int, **kw):
    session.add(Announcement(id=anno_id, url=f"https://x/{anno_id}"))
    lot = Lot(
        id=lot_id, number=f"L{lot_id}", announcement_id=anno_id,
        name="Лот", enstru="Лот", kato="710000000",
        url=f"https://goszakup.gov.kz/ru/announce/index/{anno_id}", **kw,
    )
    session.add(lot)
    session.commit()
    return lot


def _fake_client(contracts):
    client = MagicMock()
    client.iter_graphql.return_value = iter(contracts)
    return client


def _patch_refs(monkeypatch):
    refs = json.loads((FIXTURES / "ref_contract_status.json").read_text())
    items = {int(i["id"]): i for i in refs["items"]}
    monkeypatch.setattr("goszakup.jobs.contracts.ref_items", lambda c, name: items)


def test_sync_creates_contract_and_winner(session, monkeypatch):
    _patch_refs(monkeypatch)
    c = _contracts_fixture()[0]  # один unit, lotId=42654530
    lot = _make_lot(session, c["ContractUnits"][0]["lotId"], c["trdBuyId"])
    from datetime import UTC, datetime
    stats = sync_contracts(
        session, _fake_client([c]),
        dt_from=datetime(2026, 7, 25, tzinfo=UTC), dt_to=datetime(2026, 7, 27, tzinfo=UTC),
    )
    assert stats.scanned == 1 and stats.matched == 1 and stats.created == 1
    row = session.query(Contract).one()
    assert row.lot_id == lot.id
    assert row.contract_number == c["contractNumber"]
    assert float(row.contract_amount) == c["ContractUnits"][0]["totalSum"]
    assert row.status  # имя из справочника, не id
    assert not row.status.isdigit()
    session.refresh(lot)
    assert lot.winner_bin == c["supplierBiin"]
    assert stats.winners_filled == 1


def test_sync_skips_foreign_and_zero_lots(session, monkeypatch):
    _patch_refs(monkeypatch)
    fixture = _contracts_fixture()
    # В фикстуре есть договор с unit.lotId=0 — не должен падать и не матчится.
    from datetime import UTC, datetime
    stats = sync_contracts(
        session, _fake_client(fixture),
        dt_from=datetime(2026, 7, 25, tzinfo=UTC), dt_to=datetime(2026, 7, 27, tzinfo=UTC),
    )
    assert stats.scanned == len(fixture)
    assert stats.matched == 0  # ни одного нашего лота в БД
    assert session.query(Contract).count() == 0


def test_upsert_idempotent_and_not_clobbering(session, monkeypatch):
    _patch_refs(monkeypatch)
    c = _contracts_fixture()[0]
    unit = c["ContractUnits"][0]
    lot = _make_lot(session, unit["lotId"], c["trdBuyId"])
    assert upsert_contract_from_api(session, lot, c, unit, "Действует") == "created"
    # Повторный — не дубль и не "updated" без изменений.
    assert upsert_contract_from_api(session, lot, c, unit, "Действует") is None
    assert session.query(Contract).count() == 1
    # HTML успел записать fact_amount — пустое значение API его не затирает.
    row = session.query(Contract).one()
    row.fact_amount = 999
    session.commit()
    c2 = {**c, "faktSum": None}
    upsert_contract_from_api(session, lot, c2, unit, "")
    session.refresh(row)
    assert float(row.fact_amount) == 999
    assert row.status == "Действует"  # пустой статус не затёр


def test_winner_not_overwritten(session):
    lot = _make_lot(
        session, 1, 100, winner_bin="111", winner_name="ТОО Из HTML",
    )
    changed = apply_winner_from_contract(lot, {"supplierBiin": "222", "supplierFio": "Другой"})
    assert not changed
    assert lot.winner_bin == "111"


def test_fact_amount_only_for_single_lot_contract(session, monkeypatch):
    _patch_refs(monkeypatch)
    c = _contracts_fixture()[0]
    multi = {
        **c,
        "ContractUnits": [
            c["ContractUnits"][0],
            {**c["ContractUnits"][0], "lotId": 777},
        ],
    }
    lot = _make_lot(session, c["ContractUnits"][0]["lotId"], c["trdBuyId"])
    upsert_contract_from_api(session, lot, multi, multi["ContractUnits"][0], "Действует")
    row = session.query(Contract).one()
    # У мультилотового договора faktSum неатрибутируем лоту.
    assert row.fact_amount is None
