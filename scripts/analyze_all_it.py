"""Прогон analyze_and_save() по ВСЕМ IT-лотам через новый pipeline.

Использует:
- Rules (если confidence ≥ 0.85) — мгновенно, без LLM, без сети.
- SimHash-дедупликацию — копирует от клонов.
- Extractive tz_summary — заполнит даже без скачанного ТЗ (из lot.name).
- LLM-fallback — только когда правила не уверены (≈16% IT-лотов).

Идемпотентен: повторный запуск пропускает уже актуальные анализы
(`prev.analyzer_version IN (LLM_VERSION, RULES_VERSION)` + tz_sha совпал).

Можно запускать пока работает goszakup-worker — но не одновременно с daily
(оба будут писать в lot_analyses).
"""

from __future__ import annotations

import logging
import sys
import time
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from goszakup.classify.llm import analyze_and_save
from goszakup.db.engine import SessionLocal
from goszakup.db.models import Announcement, Lot, LotAnalysis

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("analyze_all_it")
log.setLevel(logging.INFO)

# Лёгкий pacing на случай если внутри какой-то части пойдёт LLM-fallback —
# держит Cerebras под лимитом, не дёргая остальные rule-based вызовы.
PACING_SEC = 0.3


def main() -> int:
    with SessionLocal() as s:
        candidates = s.scalars(
            select(Lot)
            .where(Lot.it_category.isnot(None))
            .options(
                selectinload(Lot.customer),
                selectinload(Lot.analysis),
                selectinload(Lot.announcement).selectinload(Announcement.documents),
            )
            .order_by(Lot.id)
        ).all()

        total = len(candidates)
        log.info("кандидатов: %d", total)

        stats = Counter()
        t0 = time.monotonic()
        for i, lot in enumerate(candidates, 1):
            if i > 1:
                time.sleep(PACING_SEC)
            try:
                ok = analyze_and_save(s, lot)
            except Exception as e:  # noqa: BLE001
                log.warning("[%d/%d] lot %s exception: %s", i, total, lot.id, e)
                s.rollback()
                stats["error"] += 1
                continue

            if not ok:
                stats["skipped"] += 1
                continue

            s.commit()
            a = s.scalar(select(LotAnalysis).where(LotAnalysis.lot_id == lot.id))
            if a is None:
                stats["skipped"] += 1
                continue
            # Различаем источник по analyzer_version и reused_from_lot_id.
            if a.reused_from_lot_id is not None:
                stats["reused"] += 1
                source = f"reused#{a.reused_from_lot_id}"
            elif (a.analyzer_version or "").startswith("rules-"):
                stats["rules"] += 1
                source = "rules"
            else:
                stats["llm"] += 1
                source = "llm"
            stats[f"cat:{a.dev_category}"] += 1
            if i % 25 == 0 or i == total:
                rate = i / (time.monotonic() - t0)
                eta = (total - i) / rate if rate else 0
                log.info(
                    "[%d/%d] %s lot=%s cat=%s | rate=%.1f/s eta=%.0fs",
                    i, total, source, lot.id, a.dev_category, rate, eta,
                )

        log.info("=== FINAL ===")
        for key, cnt in sorted(stats.items()):
            log.info("  %s: %d", key, cnt)
        log.info("total=%d, elapsed=%.0fs", total, time.monotonic() - t0)
        return 0


if __name__ == "__main__":
    sys.exit(main())
