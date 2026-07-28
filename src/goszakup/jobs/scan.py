"""Ad-hoc сканирование с UI: создаёт ScrapeRun под произвольные SearchParams.

Отличия от `jobs/ingest.py`: здесь фильтр — kato/amount/status/IT-категории,
а не БИН+год. Сам прогон исполняется `queue.actors.scan_actor`.

Документы и LLM включаются опционально (режим в форме); по умолчанию
сценарий «как daily» — с документами и анализом.
"""

from __future__ import annotations

import logging

from ..db.engine import SessionLocal, init_db
from ..db.models import ScrapeRun
from ..scraper.katos import region_name
from ..scraper.statuses import ACTUAL_STATUSES, PAST_STATUSES, STATUS_NAMES
from .ingest import find_active_run

log = logging.getLogger(__name__)

MODE_LISTING = "listing"          # только листинг, без деталей
MODE_NO_HEAVY = "no_heavy"        # детали (organizer, контакты, договоры) без docs/LLM
MODE_FULL = "full"                # как daily — листинг + детали + документы + LLM

ALL_MODES = (MODE_LISTING, MODE_NO_HEAVY, MODE_FULL)


def mode_flags(mode: str) -> tuple[bool, bool, bool]:
    """Раскладывает строковый режим в (listing_only, with_docs, with_llm)."""
    if mode == MODE_LISTING:
        return True, False, False
    if mode == MODE_NO_HEAVY:
        return False, False, False
    if mode == MODE_FULL:
        return False, True, True
    raise ValueError(f"unknown scan mode: {mode!r}")


def _fmt_amount(v: int | None) -> str | None:
    if v is None:
        return None
    if v >= 1_000_000 and v % 1_000_000 == 0:
        return f"{v // 1_000_000}млн"
    if v >= 1_000 and v % 1_000 == 0:
        return f"{v // 1_000}к"
    return f"{v:,}".replace(",", " ")


def _fmt_statuses(codes: list[int]) -> str | None:
    if not codes:
        return None
    code_set = set(codes)
    if code_set == set(ACTUAL_STATUSES):
        return "все актуальные"
    if code_set == set(PAST_STATUSES):
        return "все прошедшие"
    if len(codes) <= 3:
        return ", ".join(STATUS_NAMES.get(c, str(c)) for c in codes)
    return f"{len(codes)} статусов"


_MODE_LABELS = {
    MODE_LISTING: "только листинг",
    MODE_NO_HEAVY: "без LLM/документов",
    MODE_FULL: "полный",
}


def build_note(
    *,
    kato: str,
    amount_from: int,
    amount_to: int | None,
    status_codes: list[int],
    categories: list[str],
    mode: str,
) -> str:
    parts: list[str] = ["Скан"]
    parts.append(region_name(kato) if kato else "Весь РК")

    af = _fmt_amount(amount_from)
    at = _fmt_amount(amount_to)
    if af and at:
        parts.append(f"от {af} до {at} ₸")
    elif af:
        parts.append(f"от {af} ₸")
    elif at:
        parts.append(f"до {at} ₸")

    stat = _fmt_statuses(status_codes)
    if stat:
        parts.append(stat)

    if categories:
        parts.append("Категории: " + ", ".join(categories))

    parts.append(_MODE_LABELS.get(mode, mode))
    return " · ".join(parts)


def create_scan_run(
    *,
    kato: str,
    amount_from: int,
    amount_to: int | None,
    status_codes: list[int],
    categories: list[str],
    mode: str,
) -> int:
    """Создаёт ScrapeRun под ad-hoc сканирование и возвращает его id.

    Бросает RuntimeError, если активный run уже идёт — Crawl-delay на goszakup
    глобальный, две параллельных листинг-сессии бьют по одному и тому же лимиту.
    """
    if mode not in ALL_MODES:
        raise ValueError(f"unknown mode: {mode}")
    if amount_from < 0:
        raise ValueError("amount_from must be >= 0")
    if amount_to is not None and amount_to < amount_from:
        raise ValueError("amount_to < amount_from")

    init_db()
    with SessionLocal() as session:
        existing = find_active_run(session)
        if existing is not None:
            raise RuntimeError(
                f"уже идёт прогон #{existing.id} (старт {existing.started_at})"
            )
        note = build_note(
            kato=kato,
            amount_from=amount_from,
            amount_to=amount_to,
            status_codes=status_codes,
            categories=categories,
            mode=mode,
        )
        run = ScrapeRun(preset_id=None, note=note)
        session.add(run)
        session.commit()
        return run.id
