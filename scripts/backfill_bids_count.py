"""Бэкофилл `Lot.bids_count` по уже накопленным заявкам.

К goszakup НЕ ходит: число участников целиком выводится из `lot_bids`,
которые синк уже собрал. Заполняем только лоты объявлений с непустым
`bids_synced_at` — именно эта отметка отличает «опросили, заявок не было»
(ставим 0) от «не опрашивали» (оставляем NULL); без неё ноль был бы враньём.

Usage:
    python -m scripts.backfill_bids_count [--dry-run]
"""

import logging
import sys

from sqlalchemy import func, select, update

from goszakup.db.engine import SessionLocal
from goszakup.db.models import Announcement, Lot, LotBid

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("backfill_bids_count")


def main() -> int:
    dry = "--dry-run" in sys.argv
    polled = select(Announcement.id).where(Announcement.bids_synced_at.is_not(None))
    counted = (
        select(func.count(LotBid.id))
        .where(LotBid.lot_id == Lot.id)
        .scalar_subquery()
    )
    with SessionLocal() as s:
        target = s.scalar(
            select(func.count(Lot.id)).where(Lot.announcement_id.in_(polled))
        )
        log.info("лотов в опрошенных объявлениях: %s", target)
        if dry:
            return 0
        s.execute(
            update(Lot)
            .where(Lot.announcement_id.in_(polled))
            .values(bids_count=counted)
        )
        s.commit()
        rows = s.execute(
            select(Lot.bids_count, func.count(Lot.id))
            .where(Lot.bids_count.is_not(None))
            .group_by(Lot.bids_count)
            .order_by(Lot.bids_count)
            .limit(10)
        ).all()
        log.info("распределение (участников → лотов): %s", rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
