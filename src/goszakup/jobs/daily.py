"""Ежедневный обход всех активных preset'ов."""

from __future__ import annotations

import logging
import time

from sqlalchemy import select

from ..db.engine import SessionLocal, init_db
from ..db.models import Preset
from .run_preset import run_preset

log = logging.getLogger(__name__)


def run_all_active() -> None:
    init_db()
    with SessionLocal() as s:
        presets = s.scalars(
            select(Preset).where(Preset.active.is_(True)).order_by(Preset.id)
        ).all()
        ids = [(p.id, p.name) for p in presets]

    log.info("active presets: %d", len(ids))
    for pid, name in ids:
        log.info("=== preset %d: %s ===", pid, name)
        # каждую итерацию открываем свежую сессию ради консистентности run.id
        with SessionLocal() as s:
            preset = s.get(Preset, pid)
        try:
            t0 = time.monotonic()
            run = run_preset(preset=preset, download_docs=True)
            log.info(
                "preset %d done in %.1fs: listing=%d new=%d updated=%d docs=%d errors=%d",
                pid,
                time.monotonic() - t0,
                run.listing_count,
                run.new_lots,
                run.updated_lots,
                run.new_documents,
                run.errors,
            )
        except Exception as e:
            log.exception("preset %d failed: %s", pid, e)


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_all_active()
