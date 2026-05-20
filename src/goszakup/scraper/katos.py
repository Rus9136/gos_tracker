"""Справочник КАТО регионов РК для фильтра filter[kato].

Источник: AJAX-эндпоинт goszakup `/ru/search/getKato` (см. `tools/dump_katos.py`).
Это «настоящий» справочник, который форма goszakup сама использует для
автокомплита. Любой код, которого нет в этом списке, goszakup трактует как
«не задано» и отдаёт fallback-выборку ~4290 лотов (≈ полстраны без статус-
фильтра), что когда-то приводило к ложным дублирующимся прогонам по
Туркестанской и Абайской.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    code: str
    name: str


REGIONS: list[Region] = [
    Region("110000000", "Акмолинская область"),
    Region("150000000", "Актюбинская область"),
    Region("190000000", "Алматинская область"),
    Region("230000000", "Атырауская область"),
    Region("270000000", "Западно-Казахстанская область"),
    Region("310000000", "Жамбылская область"),
    Region("350000000", "Карагандинская область"),
    Region("390000000", "Костанайская область"),
    Region("430000000", "Кызылординская область"),
    Region("470000000", "Мангистауская область"),
    Region("610000000", "Туркестанская область"),
    Region("550000000", "Павлодарская область"),
    Region("590000000", "Северо-Казахстанская область"),
    Region("630000000", "Восточно-Казахстанская область"),
    Region("100000000", "Абайская область"),
    Region("330000000", "Жетысуская область"),
    Region("620000000", "Улытауская область"),
    Region("710000000", "Астана"),
    Region("750000000", "Алматы"),
    Region("790000000", "Шымкент"),
]

BY_CODE: dict[str, Region] = {r.code: r for r in REGIONS}
BY_NAME: dict[str, Region] = {r.name: r for r in REGIONS}


def region_name(code: str | None) -> str | None:
    if not code:
        return None
    r = BY_CODE.get(code)
    return r.name if r else None
