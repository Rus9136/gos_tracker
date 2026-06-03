"""Снятие лотов с «актуальных» по истечении срока приёма заявок.

goszakup не всегда меняет статус сразу после дедлайна — лот может висеть в
«Опубликован (прием заявок)» ещё какое-то время. Поэтому полагаться только на
`status_code` недостаточно: дополнительно гасим `is_actual` по фактическому
времени окончания приёма заявок (`Announcement.application_end`, UTC).

Лот из БД не удаляется — только сбрасывается флаг, как и просил пользователь.
Идемпотентно: повторный прогон ничего не трогает (WHERE is_actual=True).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db.models import Announcement, Lot

log = logging.getLogger(__name__)


def expire_actual_lots(session: Session, *, now: datetime | None = None) -> int:
    """Сбросить is_actual у актуальных лотов с истёкшим application_end.

    Возвращает число затронутых лотов. `now` инжектится в тестах.
    """
    cutoff = now or datetime.now(UTC)
    expired_anno = (
        select(Announcement.id)
        .where(Announcement.application_end.isnot(None))
        .where(Announcement.application_end < cutoff)
    )
    result = session.execute(
        update(Lot)
        .where(Lot.is_actual.is_(True))
        .where(Lot.announcement_id.in_(expired_anno))
        .values(is_actual=False)
    )
    session.commit()
    count = result.rowcount or 0
    if count:
        log.info("expire_actual_lots: снято с актуальных %d лотов", count)
    return count
