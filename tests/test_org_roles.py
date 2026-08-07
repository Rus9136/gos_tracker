"""Роли организаций: /organizations и счётчики — только закупающие.

`organizations` — одна таблица на заказчиков, организаторов и поставщиков
(orgs.py объясняет почему). До этих условий страница «Заказчики и
организаторы» листала всю таблицу: на проде из 17273 строк закупающих 7130,
а 6078 — чистые поставщики; они шли в список с нулём лотов, и на них же
считался счётчик вкладки.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from goszakup.db.models import Announcement, Contract, Lot, LotBid, Organization
from goszakup.orgs import (
    buyer_condition,
    customer_condition,
    organizer_condition,
    supplier_condition,
)
from goszakup.web import app as app_mod


@pytest.fixture
def orgs(db_session):
    """Заказчик, организатор и три поставщика — по всем трём путям наполнения."""
    customer = Organization(bin="100000000001", name="ГУ Заказчик")
    organizer = Organization(bin="100000000002", name="ГУ Организатор")
    winner = Organization(bin="200000000001", name="ТОО Победитель")
    bidder = Organization(bin="200000000002", name="ТОО Участник")
    contractor = Organization(bin="200000000003", name="ТОО Подрядчик")
    db_session.add_all([customer, organizer, winner, bidder, contractor])
    db_session.flush()

    anno = Announcement(id=777001, url="https://x/777001", organizer_id=organizer.id)
    db_session.add(anno)
    lot = Lot(
        id=777001,
        number="777001-1",
        announcement_id=anno.id,
        name="Лот",
        url="https://x/777001",
        customer_id=customer.id,
        plan_amount=1_000_000,
        winner_bin=winner.bin,
        winner_name=winner.name,
    )
    db_session.add(lot)
    db_session.add(
        LotBid(id=777001, lot_id=lot.id, supplier_bin=bidder.bin, status="Подано")
    )
    # Подрядчик — только FK договора, без winner_bin и заявки: так приходят
    # поставщики из contracts-sync.
    db_session.flush()
    db_session.add(
        Contract(lot_id=lot.id, contract_number="c-777001", supplier_id=contractor.id)
    )
    db_session.commit()
    return {"customer": customer, "organizer": organizer, "winner": winner,
            "bidder": bidder, "contractor": contractor}


def _names(session, condition):
    return set(
        session.scalars(select(Organization.name).where(condition)).all()
    )


def test_conditions_split_roles(db_session, orgs):
    assert _names(db_session, customer_condition()) == {"ГУ Заказчик"}
    assert _names(db_session, organizer_condition()) == {"ГУ Организатор"}
    assert _names(db_session, buyer_condition()) == {"ГУ Заказчик", "ГУ Организатор"}
    # Три пути наполнения: победитель и заявка приезжают строкой БИН,
    # поставщик по договору — FK.
    assert _names(db_session, supplier_condition()) == {
        "ТОО Победитель",
        "ТОО Участник",
        "ТОО Подрядчик",
    }


def test_supplier_only_orgs_excluded_from_counter(db_session, orgs):
    total = db_session.scalar(select(func.count(Organization.id)))
    buyers = db_session.scalar(
        select(func.count(Organization.id)).where(buyer_condition())
    )
    assert total == 5
    assert buyers == 2


def test_organizations_page_lists_buyers_only(db_session, orgs):
    # Кеш счётчиков живёт 60с и ключуется по uid — между тестами он врёт.
    app_mod._nav_cache.clear()
    with TestClient(app_mod.app) as client:
        html = client.get("/organizations").text
        assert "ГУ Заказчик" in html
        assert "ГУ Организатор" in html
        assert "ТОО Победитель" not in html
        assert "ТОО Участник" not in html
        assert "ТОО Подрядчик" not in html
        # Подсказка про поставщиков вместо молчаливого сокрытия.
        assert "только как поставщики" in html

        only_customers = client.get("/organizations?role=customer").text
        assert "ГУ Заказчик" in only_customers
        assert "ГУ Организатор" not in only_customers

        only_organizers = client.get("/organizations?role=organizer").text
        assert "ГУ Организатор" in only_organizers
        assert "ГУ Заказчик" not in only_organizers


def test_search_count_matches_filtered_rows(db_session, orgs):
    app_mod._nav_cache.clear()
    with TestClient(app_mod.app) as client:
        html = client.get("/organizations?q=Организатор").text
        # Счётчик пагинации раньше считался по всей таблице и врал при поиске.
        assert "из 1 организаций" in html
