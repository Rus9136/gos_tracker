"""Self-healing: дозаполнение объявлений-заглушек без деталей.

listing_actor коммитит лоты пачками, но detail_actor ставит в очередь ТОЛЬКО
после полного обхода. Если листинг оборвётся (5xx/timeout на середине), уже
закоммиченные лоты остаются со stub-объявлением (только id+url), а на ретрае
они уже «не новые» — детали к ним не приедут никогда (Гейт 2, P0 №7-adjacent).

`_ensure_announcement_stub` создаёт заглушку без `number`; `_save_announcement`
проставляет его при детализации. Значит `number IS NULL` = детали не скачаны.
Reconcile находит такие объявления у актуальных лотов и просит детали снова.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Announcement, Lot

# Потолок на один прогон reconcile — не устраивать лавину detail-запросов к
# goszakup, если заглушек накопилось много (throttle разрулит, но темп бережём).
DEFAULT_LIMIT = 200


def find_orphan_stub_annos(session: Session, limit: int = DEFAULT_LIMIT) -> list[int]:
    """anno_id объявлений-заглушек (number IS NULL) с ≥1 актуальным лотом."""
    rows = session.execute(
        select(Announcement.id)
        .join(Lot, Lot.announcement_id == Announcement.id)
        .where(Announcement.number.is_(None), Lot.is_actual.is_(True))
        .group_by(Announcement.id)
        .limit(limit)
    ).scalars().all()
    return list(rows)
