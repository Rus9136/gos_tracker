"""Парсинг вкладки «Информация о победителях» (tab=winners)."""

from bs4 import BeautifulSoup

from goszakup.scraper.announce import (
    AnnouncementDetail,
    LotDetail,
    _parse_winners,
    _winners_possible,
)

# Структура таблицы снята с живого объявления 17312158 (2026-07-16).
WINNERS_HTML = """
<table class="table">
  <tr>
    <th>Номер лота</th><th>Наименование лота</th>
    <th>Плановая сумма лота, тенге</th><th>Статус лота</th>
    <th>Победитель</th><th>Поставщик, занявший второе место</th>
  </tr>
  <tr>
    <td>87320223-ОИ2</td><td>Услуги по установке/настройке ПО</td>
    <td>1 320 000.00</td><td>Закупка состоялась</td>
    <td>240440042226 Товарищество с ограниченной ответственностью "Рога"</td>
    <td></td>
  </tr>
</table>
"""

EMPTY_HTML = "<table><tr><th>Номер лота</th><th>Победитель</th></tr></table>"


def _lot(status: str) -> LotDetail:
    return LotDetail(
        number="1", customer_bin="", customer_name="", enstru="", name="",
        extra="", price_per_unit=None, quantity=None, unit="",
        plan_amount=None, amount_y1=None, amount_y2=None, amount_y3=None,
        status_name=status,
    )


def test_parse_winners_row():
    detail = AnnouncementDetail(id=1, url="")
    _parse_winners(BeautifulSoup(WINNERS_HTML, "lxml"), detail)
    assert len(detail.winners) == 1
    w = detail.winners[0]
    assert w.lot_number == "87320223-ОИ2"
    assert w.winner_bin == "240440042226"
    assert w.winner_name.startswith("Товарищество")
    assert w.plan_amount == 1_320_000.0
    assert w.lot_status == "Закупка состоялась"
    assert w.second_bin == "" and w.second_name == ""


def test_parse_winners_empty_table():
    detail = AnnouncementDetail(id=1, url="")
    _parse_winners(BeautifulSoup(EMPTY_HTML, "lxml"), detail)
    assert detail.winners == []


def test_winners_possible_gate():
    open_only = AnnouncementDetail(id=1, url="")
    open_only.lots = [_lot("Опубликован (прием заявок)")]
    assert not _winners_possible(open_only)

    finished = AnnouncementDetail(id=2, url="")
    finished.lots = [_lot("Опубликован (прием заявок)"), _lot("Закупка состоялась")]
    assert _winners_possible(finished)

    # Лоты не распарсились — перестраховываемся и дёргаем вкладку.
    assert _winners_possible(AnnouncementDetail(id=3, url=""))
