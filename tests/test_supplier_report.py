"""Отчёт по поставщикам: агрегация трёх источников и обогащение контактов."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Announcement, Contract, Lot, LotBid, Organization
from goszakup.jobs.supplier_contacts import (
    apply_subject,
    fetch_subject,
    orgs_to_sync,
)
from goszakup.jobs.supplier_report import (
    SupplierFilters,
    build_supplier_report,
    render_csv,
)


@pytest.fixture
def session():
    init_db()
    with SessionLocal() as s:
        for model in (LotBid, Contract, Lot, Announcement, Organization):
            s.query(model).delete()
        s.commit()
        yield s


def _lot(session, lot_id, *, winner_bin=None, winner_name=None, **kw):
    anno_id = 900000 + lot_id
    session.add(Announcement(id=anno_id, url=f"https://x/{anno_id}"))
    lot = Lot(
        id=lot_id, number=f"L{lot_id}", announcement_id=anno_id,
        name="Лот", url=f"https://x/{anno_id}",
        winner_bin=winner_bin, winner_name=winner_name,
        plan_amount=1_000_000, **kw,
    )
    session.add(lot)
    session.commit()
    return lot


def test_report_merges_wins_bids_and_contacts(session):
    _lot(session, 1, winner_bin="111", winner_name="ТОО Альфа",
         enstru_code="262013.000.000011", enstru="Компьютер")
    _lot(session, 2, winner_bin="111", winner_name="ТОО Альфа",
         enstru_code="262013.000.000011", enstru="Компьютер")
    # Договор уточняет сумму первого лота (700к вместо плановой 1М).
    session.add(Contract(lot_id=1, contract_number="c1", contract_amount=700_000))
    # Проигранная заявка конкурента и «Второй победитель» Альфы по лоту 2.
    session.add(LotBid(id=10, lot_id=2, supplier_bin="222",
                       supplier_name="ТОО Бета", status="Подано"))
    session.add(LotBid(id=11, lot_id=2, supplier_bin="111",
                       supplier_name="ТОО Альфа", status="Победитель"))
    session.add(Organization(bin="111", name="ТОО Альфа",
                             email="a@a.kz", phone="+7 700"))
    session.commit()

    rows = build_supplier_report(session, SupplierFilters(enstru_code="262013"))
    by_bin = {r.bin: r for r in rows}
    alpha, beta = by_bin["111"], by_bin["222"]
    assert alpha.wins == 2
    assert alpha.won_total == 700_000 + 1_000_000
    assert alpha.email == "a@a.kz" and alpha.phone == "+7 700"
    # Бета не выигрывала, но видна как участник — это и есть «кто проигрывает».
    assert beta.wins == 0 and beta.bids == 1
    # Победители сортируются выше участников.
    assert rows[0].bin == "111"

    # Фильтр по коду отсекает всё при чужом префиксе.
    assert build_supplier_report(session, SupplierFilters(enstru_code="999")) == []

    csv_text = render_csv(rows)
    assert "ТОО Альфа" in csv_text and "+7 700" in csv_text


def test_orgs_to_sync_creates_missing_and_skips_fresh(session):
    # Победитель, добытый HTML-ом: строки в organizations нет вовсе.
    _lot(session, 1, winner_bin="333", winner_name="ТОО Гамма")
    orgs = orgs_to_sync(session)
    assert [o.bin for o in orgs] == ["333"]
    assert orgs[0].name == "ТОО Гамма"

    # После простановки отметки организация выпадает из выборки.
    from datetime import UTC, datetime

    orgs[0].contacts_synced_at = datetime.now(UTC)
    session.commit()
    assert orgs_to_sync(session) == []


def test_fetch_subject_falls_back_to_iin_and_apply_is_non_destructive():
    client = MagicMock()
    client.graphql.side_effect = [
        ({"Subjects": []}, None),  # по bin пусто (это ИП)
        ({"Subjects": [{"email": "x@x.kz", "phone": "", "website": None,
                        "Address": [{"address": "г. Алматы"}]}]}, None),
    ]
    subject = fetch_subject(client, "880720300492")
    assert subject["email"] == "x@x.kz"
    assert client.graphql.call_count == 2

    org = Organization(bin="880720300492", name="ИП", phone="+7 701 сохранён")
    assert apply_subject(org, subject) is True
    assert org.email == "x@x.kz"
    # Пустые поля реестра не затирают уже известное.
    assert org.phone == "+7 701 сохранён"
    assert org.address == "г. Алматы"
