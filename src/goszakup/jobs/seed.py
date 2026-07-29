"""Seed дефолтных preset'ов: по одному на каждый регион РК, актуальные статусы.

Идея: preset решает «что мы СКАЧИВАЕМ». Вертикаль проставляется на upsert
для всех лотов автоматически, поэтому фильтровать на preset-уровне не нужно —
фильтр доступен в UI. Если позже понадобится, можно завести специализированные
preset'ы (например, «РК / Только медицина»).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..config import MIN_AMOUNT
from ..db.engine import SessionLocal, init_db
from ..db.models import Preset
from ..scraper.katos import REGIONS
from ..scraper.statuses import ACTUAL_STATUSES

log = logging.getLogger(__name__)


def seed_regional_presets() -> int:
    """Создаёт по одному preset'у на каждый регион (idempotent)."""
    init_db()
    created = 0
    with SessionLocal() as s:
        for r in REGIONS:
            name = f"{r.name} — актуальные"
            existing = s.scalar(select(Preset).where(Preset.name == name))
            if existing:
                continue
            s.add(
                Preset(
                    name=name,
                    kato=r.code,
                    amount_from=MIN_AMOUNT,
                    amount_to=None,
                    status_codes=list(ACTUAL_STATUSES),
                    categories=None,
                    active=True,
                )
            )
            created += 1
        s.commit()
    log.info("seeded %d presets", created)
    return created


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(levelname)s %(name)s: %(message)s")
    n = seed_regional_presets()
    print(f"created presets: {n}")
