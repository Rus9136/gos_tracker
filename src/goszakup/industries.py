"""Отрасль организации (`Organization.industry`) — слаг из реестра INDUSTRIES.

Два источника, в порядке приоритета:
1. Ключевые слова в названии — только для медицины (`keywords`). Покрывают
   всех сразу, включая четверть заказчиков без БИН (правило #4), и точнее
   реестра: у районной больницы в ОКЭД может стоять «аптечная розница»
   (47731 у Аршалынской ЦРБ, замерено 2026-09-02).
2. Код ОКЭД из реестра участников OWS (`Subjects.okedList`, у больниц
   86101, у школ 85310, у акиматов 84111) — хранится в `Organization.oked`,
   тянется `jobs/industry_sync.py` по одному запросу на БИН (серверного
   фильтра по ОКЭД нет). Класс — первые две цифры, как разделы NACE.

Слаг ставится один раз при создании организации и бэкофиллом
(`backfill_industries`); уже присвоенный не перезаписывается — как и
вертикаль лота (правило #24), чтобы правка словаря не дёргала выборки
задним числом. Пересчёт — только явный `backfill_industries(force=True)`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import Organization


@dataclass(frozen=True)
class Industry:
    slug: str
    label: str
    # Первые две цифры ОКЭД (раздел по NACE rev.2).
    oked_prefixes: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    # Подстроки, при которых совпадение keywords НЕ считается.
    exclude: tuple[str, ...] = ()


INDUSTRIES: dict[str, Industry] = {
    "med": Industry(
        slug="med",
        label="Здравоохранение",
        oked_prefixes=("86",),
        keywords=(
            "больниц",
            "поликлин",
            "госпитал",
            "диспансер",
            "медицин",
            "здравоохран",
            "перинатал",
            "родильн",
            "скорой помощи",
            "скорой медицинской",
            "санатор",
            "стоматолог",
            "амбулатор",
            "клиническ",
            "клиника",
            "хоспис",
            "онколог",
            "кардиолог",
            "психиатр",
            "нарколог",
            "фтизиатр",
            "туберкул",
            "инфекцион",
            "центр крови",
            "санитарно-эпидем",
            "эпидемиолог",
            "лечебн",
            # казахские названия
            "аурухана",
            "емхана",
            "денсаулық",
            "медициналық",
            "перзентхана",
            "жедел жәрдем",
            "қан орталығы",
        ),
        # Не медучреждения, хотя слова медицинские: санаторные ясли-сады и
        # школы-интернаты, медколледжи и медуниверситеты (это образование),
        # фитосанитария и ветеринария.
        exclude=(
            "фитосанитар",
            "ветеринар",
            "ясли",
            "детский сад",
            "школ",
            "интернат",
            "колледж",
            "университет",
            "академи",
            "образован",
        ),
    ),
    "edu": Industry("edu", "Образование", oked_prefixes=("85",)),
    "gov": Industry("gov", "Госуправление", oked_prefixes=("84",)),
    "social": Industry("social", "Соцобслуживание", oked_prefixes=("87", "88")),
    "culture": Industry(
        "culture", "Культура и спорт", oked_prefixes=("90", "91", "93")
    ),
    "science": Industry("science", "Наука", oked_prefixes=("72",)),
    "utilities": Industry(
        "utilities", "ЖКХ и энергетика", oked_prefixes=("35", "36", "37", "38", "39")
    ),
    "transport": Industry(
        "transport", "Транспорт", oked_prefixes=("49", "50", "51", "52", "53")
    ),
    "construction": Industry(
        "construction", "Строительство", oked_prefixes=("41", "42", "43")
    ),
    "agro": Industry("agro", "Сельское хозяйство", oked_prefixes=("01", "02", "03")),
    "mining": Industry(
        "mining", "Добыча", oked_prefixes=("05", "06", "07", "08", "09")
    ),
    "manufacturing": Industry(
        "manufacturing",
        "Производство",
        oked_prefixes=tuple(f"{n:02d}" for n in range(10, 34)),
    ),
    "trade": Industry("trade", "Торговля", oked_prefixes=("45", "46", "47")),
    "it": Industry("it", "IT и связь", oked_prefixes=("61", "62", "63")),
    "finance": Industry("finance", "Финансы", oked_prefixes=("64", "65", "66")),
}

_BY_OKED_PREFIX: dict[str, str] = {
    p: ind.slug for ind in INDUSTRIES.values() for p in ind.oked_prefixes
}


def industry_from_oked(oked: str | int | None) -> str | None:
    code = str(oked or "").strip()
    if not code.isdigit():
        return None
    return _BY_OKED_PREFIX.get(code[:2])


def classify_industry(name: str | None, oked: str | int | None = None) -> str | None:
    text = (name or "").lower().replace("ё", "е")
    for ind in INDUSTRIES.values():
        if not ind.keywords or any(x in text for x in ind.exclude):
            continue
        if any(k in text for k in ind.keywords):
            return ind.slug
    return industry_from_oked(oked)


def backfill_industries(session: Session, *, force: bool = False) -> int:
    """Проставить отрасль по названию и ОКЭД всем организациям без неё.

    Возвращает число изменённых строк. Чистая работа по БД, к goszakup не ходит.
    """
    stmt = select(Organization)
    if not force:
        stmt = stmt.where(Organization.industry.is_(None))
    changed = 0
    for org in session.scalars(stmt.execution_options(yield_per=1000)):
        slug = classify_industry(org.name, org.oked)
        if slug != org.industry:
            org.industry = slug
            changed += 1
    session.commit()
    return changed
