"""Окно инкрементального синка с OWS: водяной знак по ScrapeRun.

Отдельной таблицы состояния нет намеренно: последний успешный прогон с
note-тегом — и есть водяной знак (паттерн ad-hoc прогонов scan/ingest).
Запас в 1 час перекрывает недокачанный хвост предыдущего окна и рассинхрон
часов; потолок в 7 дней защищает от прохода по полумиллиону строк после
долгого простоя — тогда честнее прогнать пресеты вручную.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import MIN_AMOUNT
from ..db.models import Preset, ScrapeRun
from ..scraper.statuses import ACTUAL_STATUSES

log = logging.getLogger(__name__)

NOTE_PREFIX_DAILY = "api-daily"
NOTE_PREFIX_CONTRACTS = "contracts-sync"

WINDOW_MARGIN = timedelta(hours=1)
FIRST_RUN_SPAN = timedelta(hours=48)
MAX_SPAN = timedelta(days=7)


def last_success_started_at(session: Session, note_prefix: str) -> datetime | None:
    started = session.scalar(
        select(ScrapeRun.started_at)
        .where(
            ScrapeRun.note.like(f"{note_prefix}%"),
            ScrapeRun.finished_at.is_not(None),
            ScrapeRun.errors == 0,
        )
        .order_by(ScrapeRun.started_at.desc())
        .limit(1)
    )
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started


def sync_window(
    session: Session, note_prefix: str, *, now: datetime | None = None
) -> tuple[datetime, datetime, bool]:
    """(from, to, clamped): from = водяной знак − 1ч, первый запуск − 48ч.

    clamped=True — окно обрезано потолком 7 дней (был простой): часть
    изменений источника потеряна, вызывающий обязан написать WARNING и
    посоветовать ручной прогон пресетов.
    """
    now = now or datetime.now(UTC)
    watermark = last_success_started_at(session, note_prefix)
    if watermark is None:
        return now - FIRST_RUN_SPAN, now, False
    dt_from = watermark - WINDOW_MARGIN
    floor = now - MAX_SPAN
    if dt_from < floor:
        return floor, now, True
    return dt_from, now, False


def daily_scan_params(session: Session) -> tuple[list[int], int]:
    """Статусы и минимальная сумма для общереспубликанского прохода.

    Пресеты остаются источником продуктовой конфигурации покрытия: берём
    объединение status_codes активных и min(amount_from). Пусто (все
    выключены) — фолбэк на константы, чтобы daily не ослеп молча.
    """
    presets = session.scalars(select(Preset).where(Preset.active.is_(True))).all()
    statuses: set[int] = set()
    amounts: list[int] = []
    for p in presets:
        statuses.update(p.status_codes or [])
        if p.amount_from:
            amounts.append(p.amount_from)
    return (
        sorted(statuses) or list(ACTUAL_STATUSES),
        min(amounts) if amounts else MIN_AMOUNT,
    )
