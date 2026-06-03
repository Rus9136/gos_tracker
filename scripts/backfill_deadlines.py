"""Бэкофилл `Announcement.application_end` для уже накопленных актуальных лотов.

Поле появилось вместе с кроном expire (jobs/expire.py). У лотов, добытых до
этого, дедлайн не записан, поэтому крон их не тронет. Срок окончания приёма
заявок лежит на детальной странице в `value` у `<input>` (таб general) — берём
один запрос на объявление (не четыре, как fetch_announcement).

Идёт через ОБЩИЙ с worker'ом Redis-throttle (`make_http_session`): глобальный
Crawl-delay соблюдается даже если параллельно крутится daily. Resumable:
обрабатываем только объявления с пустым application_end, коммитим после
каждого. Каждые 100 объявлений (и в конце) гасим истёкшие — выдача чистится
прогрессивно, не дожидаясь конца обхода.

Порядок — по возрастанию id объявления (старые первыми): у них дедлайн почти
всегда уже в прошлом, так что ценные «переносы в прошедшие» случаются рано.

Usage:
    python -m scripts.backfill_deadlines               # expire по now()
    python -m scripts.backfill_deadlines 2026-06-01    # expire всё с дедлайном < 1 июня
"""

import logging
import sys
from datetime import UTC, datetime

import redis
from bs4 import BeautifulSoup
from sqlalchemy import distinct, select

from goszakup.config import ANNOUNCE_URL
from goszakup.db.engine import SessionLocal
from goszakup.db.models import Announcement, Lot
from goszakup.jobs.expire import expire_actual_lots
from goszakup.queue.broker import REDIS_URL
from goszakup.queue.rate_limit import make_http_session
from goszakup.scraper.announce import AnnouncementDetail, _parse_general

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("backfill_deadlines")
log.setLevel(logging.INFO)

EXPIRE_EVERY = 100


def main() -> int:
    cutoff = None
    if len(sys.argv) > 1:
        cutoff = datetime.strptime(sys.argv[1], "%Y-%m-%d").replace(tzinfo=UTC)
        log.info("expire cutoff: дедлайн < %s", cutoff)

    with SessionLocal() as s:
        anno_ids = s.scalars(
            select(distinct(Lot.announcement_id))
            .join(Announcement, Announcement.id == Lot.announcement_id)
            .where(Lot.is_actual.is_(True))
            .where(Lot.announcement_id.isnot(None))
            .where(Announcement.application_end.is_(None))
            .order_by(Lot.announcement_id)
        ).all()

    total = len(anno_ids)
    log.info("объявлений к бэкофиллу: %d (~%.1fч обхода)", total, total * 5 / 3600)
    http = make_http_session(redis.Redis.from_url(REDIS_URL, decode_responses=True))
    filled = 0

    for i, anno_id in enumerate(anno_ids, 1):
        try:
            r = http.get(f"{ANNOUNCE_URL}/{anno_id}", params={"tab": "general"})
            r.raise_for_status()
            detail = AnnouncementDetail(id=anno_id, url=f"{ANNOUNCE_URL}/{anno_id}")
            _parse_general(BeautifulSoup(r.text, "lxml"), detail)
        except Exception as e:
            log.warning("[%d/%d] anno %s: ошибка запроса: %s", i, total, anno_id, e)
            continue

        if not detail.application_end:
            log.info("[%d/%d] anno %s: дедлайн не найден", i, total, anno_id)
            continue

        with SessionLocal() as s:
            anno = s.get(Announcement, anno_id)
            if anno is not None:
                anno.application_end = detail.application_end
                if detail.publish_date and anno.publish_date is None:
                    anno.publish_date = detail.publish_date
                s.commit()
                filled += 1
        log.info("[%d/%d] anno %s: %s", i, total, anno_id, detail.application_end)

        if i % EXPIRE_EVERY == 0:
            with SessionLocal() as s:
                n = expire_actual_lots(s, now=cutoff)
            log.info("  …промежуточный expire: снято %d (заполнено %d/%d)", n, filled, i)

    with SessionLocal() as s:
        expired = expire_actual_lots(s, now=cutoff)
    log.info("ГОТОВО: заполнено %d дедлайнов, итоговый expire снял %d", filled, expired)
    return 0


if __name__ == "__main__":
    sys.exit(main())
