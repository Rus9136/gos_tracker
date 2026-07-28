"""Прогнать LLM-анализ по всем актуальным IT-лотам без актуального tz_summary.

Одноразовый скрипт: запустили после смены ANALYZER_VERSION на _ru.
Коммитит после каждого лота, чтобы прерванный запуск не терял прогресс.
"""

import logging
import sys
import time

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from goszakup.classify.llm import ANALYZER_VERSION, analyze_and_save
from goszakup.db.engine import SessionLocal
from goszakup.db.models import Announcement, Lot, LotAnalysis

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reanalyze")
log.setLevel(logging.INFO)


def main() -> int:
    with SessionLocal() as s:
        candidates = s.scalars(
            select(Lot)
            .join(LotAnalysis, LotAnalysis.lot_id == Lot.id, isouter=True)
            .where(
                Lot.is_actual.is_(True),
                Lot.category.isnot(None),
                or_(
                    LotAnalysis.id.is_(None),
                    LotAnalysis.analyzer_version != ANALYZER_VERSION,
                    LotAnalysis.tz_summary.is_(None),
                    LotAnalysis.tz_summary == "",
                ),
            )
            .options(
                selectinload(Lot.customer),
                selectinload(Lot.analysis),
                selectinload(Lot.announcement).selectinload(Announcement.documents),
            )
        ).all()

        total = len(candidates)
        log.info("кандидатов: %d", total)

        done = 0
        skipped = 0
        t0 = time.monotonic()
        # Cerebras free-tier душит при бурстах; 1.5с между запросами держат
        # темп под лимитом и одновременно дают ретраю шанс срабатывать редко.
        REQ_PACING = 1.5
        for i, lot in enumerate(candidates, 1):
            if i > 1:
                time.sleep(REQ_PACING)
            ok = analyze_and_save(s, lot)
            if ok:
                s.commit()
                done += 1
                # `lot.analysis` после insert не всегда подгружен через
                # back_populates — перечитываем явно из БД.
                a = s.scalar(
                    select(LotAnalysis).where(LotAnalysis.lot_id == lot.id)
                )
                if a is not None:
                    summary = (a.tz_summary or "")[:80].replace("\n", " ")
                    log.info(
                        "[%d/%d] ✓ lot %s | %s | %s | %s",
                        i, total, lot.id, a.dev_category, a.analysis_confidence, summary,
                    )
                else:
                    log.info("[%d/%d] ✓ lot %s (анализ сохранён, но не прочитался)", i, total, lot.id)
            else:
                skipped += 1
                log.info("[%d/%d] ✗ lot %s skipped", i, total, lot.id)
            if i % 10 == 0:
                rate = i / (time.monotonic() - t0)
                eta = (total - i) / rate if rate else 0
                log.info(
                    "progress: %d/%d done=%d skipped=%d rate=%.2f/s eta=%.0fs",
                    i, total, done, skipped, rate, eta,
                )

        log.info("FINAL: done=%d skipped=%d total=%d", done, skipped, total)
        return 0


if __name__ == "__main__":
    sys.exit(main())
