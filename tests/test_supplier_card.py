"""Карточка поставщика: победы, проигранные заявки и цена победителя.

Полнота источников разная (правило #22), поэтому тесты фиксируют именно её:
победа приходит тремя путями, а win-rate считается только по лотам с
известной заявкой — иначе он был бы тождественно равен 100%.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goszakup.db.models import Announcement, Contract, Lot, LotBid, Organization
from goszakup.jobs.supplier_card import build_supplier_card
from goszakup.web import app as app_mod

ALPHA = "111111111111"
BETA = "222222222222"


def _lot(session, lot_id, *, customer=None, winner_bin=None, winner_name=None, **kw):
    anno_id = 880000 + lot_id
    session.add(
        Announcement(id=anno_id, url=f"https://x/{anno_id}", publish_date=None)
    )
    lot = Lot(
        id=lot_id,
        number=f"L{lot_id}-1",
        announcement_id=anno_id,
        name=f"Лот {lot_id}",
        url=f"https://x/{anno_id}",
        customer_id=customer.id if customer else None,
        plan_amount=1_000_000,
        winner_bin=winner_bin,
        winner_name=winner_name,
        **kw,
    )
    session.add(lot)
    session.flush()
    return lot


@pytest.fixture
def data(db_session):
    customer = Organization(bin="900000000001", name="ГУ Заказчик")
    alpha = Organization(bin=ALPHA, name="ТОО Альфа", email="a@a.kz")
    db_session.add_all([customer, alpha])
    db_session.flush()

    # 1: победа по winner_bin + договор на 700к (сумма договора важнее плана).
    won = _lot(db_session, 1, customer=customer, winner_bin=ALPHA,
               winner_name="ТОО Альфа", enstru="Компьютеры",
               enstru_code="262013.000.000012")
    db_session.add(
        Contract(lot_id=won.id, contract_number="c-1", contract_amount=700_000,
                 status="Исполнен")
    )
    # 2: победа только через заявку со статусом «Победитель» — winner_bin пуст.
    by_bid = _lot(db_session, 2, customer=customer, enstru="Компьютеры",
                  enstru_code="262013.000.000012")
    db_session.add(
        LotBid(id=201, lot_id=by_bid.id, supplier_bin=ALPHA,
               supplier_name="ТОО Альфа", amount=900_000, status="Победитель")
    )
    # 3: победа только через FK договора (так приходит contracts-sync).
    by_fk = _lot(db_session, 3, customer=customer, enstru="Серверы",
                 enstru_code="262020.000.000001")
    db_session.add(
        Contract(lot_id=by_fk.id, contract_number="c-3", contract_amount=500_000,
                 supplier_id=alpha.id)
    )
    # 4: проигрыш — его заявка дороже победившей.
    lost = _lot(db_session, 4, customer=customer, winner_bin=BETA,
                winner_name="ТОО Бета", enstru="Компьютеры",
                enstru_code="262013.000.000012")
    db_session.add_all([
        LotBid(id=401, lot_id=lost.id, supplier_bin=ALPHA,
               supplier_name="ТОО Альфа", amount=1_000_000, status="Второй победитель"),
        LotBid(id=402, lot_id=lost.id, supplier_bin=BETA,
               supplier_name="ТОО Бета", amount=800_000, status="Победитель"),
    ])
    db_session.commit()
    return {"customer": customer, "alpha": alpha}


def test_wins_collected_from_three_sources(db_session, data):
    card = build_supplier_card(db_session, ALPHA)
    assert card.wins_n == 3
    assert {r.lot_id for r in card.wins} == {1, 2, 3}
    # Сумма побед: 700к (договор) + 1М (плановая, договора нет) + 500к (договор).
    assert card.won_total == pytest.approx(2_200_000)
    by_lot = {r.lot_id: r for r in card.wins}
    assert (by_lot[1].amount, by_lot[1].amount_source) == (700_000, "contract")
    assert (by_lot[2].amount, by_lot[2].amount_source) == (1_000_000, "plan")


def test_loss_shows_own_and_winning_price(db_session, data):
    card = build_supplier_card(db_session, ALPHA)
    assert [r.lot_id for r in card.losses] == [4]
    loss = card.losses[0]
    assert loss.my_bid == 1_000_000
    assert loss.my_bid_status == "Второй победитель"
    assert loss.winner_bin == BETA
    assert loss.winner_amount == 800_000


def test_win_rate_counts_only_lots_with_known_bid(db_session, data):
    card = build_supplier_card(db_session, ALPHA)
    # Заявки есть по двум лотам (2 и 4), выиграл из них один → 50%,
    # а не 3/4 от всех побед: у лотов 1 и 3 заявок в БД нет вовсе.
    assert card.bid_lots_n == 2
    assert card.win_rate == 50.0
    assert card.seconds_n == 1


def test_stats_cover_all_lots_but_tables_are_capped(db_session, data):
    card = build_supplier_card(db_session, ALPHA, rows_limit=1)
    assert card.wins_n == 3
    assert card.won_total == pytest.approx(2_200_000)
    assert card.wins_shown == 1


def test_group_tables_split_customers_and_subjects(db_session, data):
    card = build_supplier_card(db_session, ALPHA)
    # Все три победы — у одного заказчика, поэтому строка одна.
    assert card.customers_n == 1
    customer = card.top_customers[0]
    assert customer.name == "ГУ Заказчик"
    assert customer.bin == "900000000001"
    assert customer.wins == 3
    assert customer.total == pytest.approx(2_200_000)
    # Заявок у этого заказчика известно две (лоты 2 и 4) — это не победы,
    # а знаменатель «сколько раз заходил» (правило #22).
    assert customer.bids == 2

    by_name = {r.name: r for r in card.top_enstru}
    assert card.enstru_n == 2
    assert by_name["Компьютеры"].wins == 2
    assert by_name["Компьютеры"].code == "262013.000.000012"
    assert by_name["Серверы"].wins == 1
    assert by_name["Серверы"].total == pytest.approx(500_000)


def test_group_tables_capped_but_count_is_full(db_session, data):
    card = build_supplier_card(db_session, ALPHA, group_rows=1)
    assert card.enstru_n == 2
    assert len(card.top_enstru) == 1
    # Обрезаем по числу побед, поэтому наверху «Компьютеры» (2), не «Серверы».
    assert card.top_enstru[0].name == "Компьютеры"


def test_unknown_bin_is_404(db_session, data):
    app_mod._nav_cache.clear()
    with TestClient(app_mod.app) as client:
        assert client.get("/supplier/000000000000").status_code == 404


def test_page_renders_wins_and_losses(db_session, data):
    app_mod._nav_cache.clear()
    with TestClient(app_mod.app) as client:
        html = client.get(f"/supplier/{ALPHA}").text
        assert "ТОО Альфа" in html
        assert "Лот 1" in html and "Лот 3" in html
        assert "Лот 4" in html
        # Победитель проигранного лота — ссылка на его карточку.
        assert f"/supplier/{BETA}" in html
        assert "нет данных" in html
