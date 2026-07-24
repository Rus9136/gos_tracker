"""Маппинг JSON OWS → dataclasses: таймзона, КАТО, статусы, ЕНС ТРУ, файлы."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from goszakup.api.mapping import (
    almaty_to_utc,
    detail_from_trd_buy,
    kato_in_region,
    listing_hit_from_lot,
)
from goszakup.jobs.run_preset import _status_code_from_name

FIXTURES = Path(__file__).parent / "fixtures" / "api"


def _listing():
    return json.loads((FIXTURES / "trd_buy_listing.json").read_text())


def _lots_listing():
    return json.loads((FIXTURES / "lots_listing.json").read_text())


def test_almaty_to_utc():
    # Сверено с боевым HTML-парсером: 19:59:23 Алматы == 14:59:23 UTC.
    assert almaty_to_utc("2026-07-28 19:59:23") == datetime(
        2026, 7, 28, 14, 59, 23, tzinfo=timezone.utc
    )
    assert almaty_to_utc(None) is None
    assert almaty_to_utc("мусор") is None


def test_kato_in_region_prefix():
    assert kato_in_region(["551010000"], "550000000")  # Павлодарская
    assert kato_in_region(["334851100"], "330000000")  # Жетысуская
    assert not kato_in_region(["334851100"], "350000000")  # не Карагандинская
    assert kato_in_region(["551010000", "710000000"], "710000000")  # любой из мест
    assert kato_in_region([], "")  # без региона — не фильтруем
    assert not kato_in_region([], "550000000")


def test_detail_from_fixture():
    tb = _listing()[0]  # anno 17379679, ЗЦП, один лот «Крупа гречневая»
    d = detail_from_trd_buy(tb)
    assert d.id == tb["id"]
    assert d.url.endswith(f"/ru/announce/index/{tb['id']}")
    assert d.number == tb["numberAnno"]
    assert d.application_end is not None and d.application_end.tzinfo is not None
    assert d.lots and d.lots[0].number == tb["Lots"][0]["lotNumber"]
    # descriptionRu — «доп. характеристика» лота.
    assert d.lots[0].extra == tb["Lots"][0]["descriptionRu"]
    # У ТЗ-подобного файла ссылка есть, у проекта договора — нет (правило #3).
    tz = [r for r in d.documents if "техническая спецификация" in r.name.lower()]
    other = [r for r in d.documents if "договор" in r.name.lower()]
    assert tz and all(r.direct_url for r in tz)
    assert other and all(r.direct_url is None for r in other)


def test_status_roundtrip_from_fixture():
    """id статуса API → имя из STATUS_NAMES → обратно в тот же код."""
    for tb in _listing():
        for lot in tb["Lots"]:
            hit = listing_hit_from_lot(lot)
            assert hit.status_name, f"нет имени для {lot['refLotStatusId']}"
            assert _status_code_from_name(hit.status_name) == lot["refLotStatusId"]


def test_listing_hit_fields():
    lot = next(l for l in _lots_listing() if l["Plans"])
    hit = listing_hit_from_lot(lot)
    assert hit.lot_id == lot["id"]
    assert hit.announcement_id == lot["trdBuyId"]
    assert hit.announcement_number == lot["trdBuyNumberAnno"]
    assert hit.announcement_url.endswith(f"/ru/announce/index/{lot['trdBuyId']}")
    assert hit.quantity == str(lot["count"])
    assert hit.plan_amount == lot["amount"]
    assert hit.customer_name == lot["customerNameRu"]
    assert hit.method == lot["RefTradeMethods"]["nameRu"]
    # ЕНС ТРУ: из Plans, когда план проиндексирован.
    assert hit.enstru == lot["Plans"][0]["RefEnstru"]["nameRu"]


def test_enstru_fallback_to_lot_name():
    """Свежие ЗЦП без плана: enstru = имя лота (оно и есть имя позиции ЕНС ТРУ)."""
    lot = next(l for l in _lots_listing() if not l["Plans"])
    hit = listing_hit_from_lot(lot)
    assert hit.enstru == lot["nameRu"]


def test_enstru_code_in_lot_detail():
    tb = _listing()[1]  # у лота есть Plans с кодом
    d = detail_from_trd_buy(tb)
    assert d.lots[0].enstru_code == tb["Lots"][0]["Plans"][0]["RefEnstru"]["code"]
    # А у ЗЦП без плана кода нет — None, не пустая строка.
    d0 = detail_from_trd_buy(_listing()[0])
    assert d0.lots[0].enstru_code is None
