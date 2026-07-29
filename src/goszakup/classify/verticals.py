"""Вертикали — кураторская таксономия рынка для SaaS (SAAS_PIVOT_PLAN.md).

Стратегия классификации лота:
1) по цифровому коду ЕНС ТРУ: первые цифры кода = раздел КПВЭД, матч по
   самому длинному префиксу реестра;
2) fallback по ключевым словам в имени ТРУ/лота (для IT — весь прежний
   классификатор classify/it.py, включая exact-словарь);
3) ничего не подошло → None («прочее», лот всё равно хранится).

Код первичен: лот с кодом 80.xx и словом «видеонаблюдение» — это «Охрана»,
а не IT, хотя keyword-правила it.py его бы взяли.

Слаги — латиница: они попадают в URL query, JSON-scope пользователей и
Lot.category. Русские имена — только для UI (VERTICAL_LABELS).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .it import classify as _it_classify


@dataclass(frozen=True)
class Vertical:
    slug: str
    name_ru: str
    # Префиксы кода ЕНС ТРУ в записи КПВЭД с точками ("26", "32.5", "81.2").
    code_prefixes: tuple[str, ...]
    keywords: tuple[re.Pattern, ...] = field(default=())
    # oklch-hue чипа в UI; «прочее»/None рисуется серым в макросе cat_tag.
    hue: int = 200


VERTICALS: list[Vertical] = [
    # 26 — компьютеры/электроника, 58 — издание ПО, 61 — телеком,
    # 62 — ИТ-услуги, 63 — информационные услуги.
    Vertical("it", "IT", ("26", "58", "61", "62", "63"), hue=200),
    # 21 — фарма, 26.6 — электромедицинское оборудование (литотриптеры,
    # рентген и т.п.; выигрывает у «26»-IT длиной префикса), 32.5 —
    # мединструменты, 86 — здравоохранение.
    Vertical("medicine", "Медицина", ("21", "26.6", "32.5", "86"), hue=80),
    # 41-43 — здания, инженерные сооружения, спецработы.
    Vertical("construction", "Стройка", ("41", "42", "43"), hue=280),
    Vertical("furniture", "Мебель", ("31",), hue=30),
    # 01 — растениеводство, 03 — рыба, 10 — пищевые продукты, 11 — напитки.
    Vertical("food", "Продукты", ("01", "03", "10", "11"), hue=160),
    # 81.2 — услуги по очистке и уборке.
    Vertical(
        "cleaning",
        "Клининг",
        ("81.2",),
        (re.compile(r"уборк|клининг|санитарн(ой|ая|ую) очистк"),),
        hue=120,
    ),
    # 80 — услуги по обеспечению безопасности.
    Vertical(
        "security",
        "Охрана",
        ("80",),
        (re.compile(r"охранн(ых|ые|ой) услуг|услуг[аи] охраны|физическ(ой|ая) охран"),),
        hue=0,
    ),
    # 14 — одежда, 32.99 — СИЗ/спецснаряжение.
    Vertical("ppe", "СИЗ и одежда", ("14", "32.99"), hue=320),
    # 29 — автотранспорт, 45.2 — ТО и ремонт авто, 49 — сухопутные перевозки.
    Vertical("transport", "Транспорт", ("29", "45.2", "49"), hue=240),
    Vertical("education", "Обучение", ("85",), hue=50),
]

VERTICAL_BY_SLUG: dict[str, Vertical] = {v.slug: v for v in VERTICALS}
VERTICAL_LABELS: dict[str, str] = {v.slug: v.name_ru for v in VERTICALS}
VERTICAL_HUES: dict[str, int] = {v.slug: v.hue for v in VERTICALS}

# (цифровой префикс без точек, слаг), длинные префиксы первыми — чтобы
# "32.5"→медицина не съедался бы более коротким префиксом другой вертикали.
_PREFIX_TABLE: list[tuple[str, str]] = sorted(
    (
        (prefix.replace(".", ""), v.slug)
        for v in VERTICALS
        for prefix in v.code_prefixes
    ),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def _classify_by_code(enstru_code: str | None) -> str | None:
    # Код вида "192021.530.000001" — раздел КПВЭД в первом сегменте.
    head = (enstru_code or "").strip().split(".", 1)[0]
    if not head.isdigit():
        return None
    for prefix, slug in _PREFIX_TABLE:
        if head.startswith(prefix):
            return slug
    return None


def classify_vertical(
    enstru_code: str | None,
    enstru_name: str | None,
    lot_name: str | None = None,
) -> str | None:
    by_code = _classify_by_code(enstru_code)
    if by_code is not None:
        return by_code
    if _it_classify(enstru_name, lot_name) is not None:
        return "it"
    haystack = f"{enstru_name or ''} || {lot_name or ''}".lower()
    for v in VERTICALS:
        for pattern in v.keywords:
            if pattern.search(haystack):
                return v.slug
    return None
