"""Бэкофилл `Lot.enstru_code` для уже накопленных IT-лотов.

Цифровой «Код ТРУ» появился вместе с фазой 2 (`jobs/run_preset._apply_enstru_code`):
её код заполняется только у новых/сменивших статус IT-лотов. У добытых раньше
`enstru_code` пустой и сам по себе не проставится, пока лот снова не зацепит
фаза 2. Здесь — разовый проход по всем IT-лотам без кода.

Код лежит ТОЛЬКО на карточке ценового предложения лота (`subpriceoffer`) — это
+1 запрос на лот, поэтому идём через ОБЩИЙ с worker'ом Redis-throttle
(`make_http_session`): глобальный Crawl-delay соблюдается даже если параллельно
крутится daily. Resumable: берём только `enstru_code IS NULL`, коммитим после
каждого лота. `trd_buy_id == announcement_id` (совпадают на goszakup).

Usage:
    python -m scripts.backfill_enstru_codes
"""

import logging
import sys

import redis
from sqlalchemy import select

from goszakup.db.engine import SessionLocal
from goszakup.db.models import Lot
from goszakup.queue.broker import REDIS_URL
from goszakup.queue.rate_limit import make_http_session
from goszakup.scraper.announce import fetch_lot_enstru_code

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("backfill_enstru_codes")
log.setLevel(logging.INFO)


def main() -> int:
    with SessionLocal() as s:
        rows = s.execute(
            select(Lot.id, Lot.announcement_id)
            .where(Lot.it_category.isnot(None))
            .where(Lot.enstru_code.is_(None))
            .where(Lot.announcement_id.isnot(None))
            .order_by(Lot.id)
        ).all()

    total = len(rows)
    log.info("IT-лотов к бэкофиллу: %d (~%.1fч обхода)", total, total * 5 / 3600)
    http = make_http_session(redis.Redis.from_url(REDIS_URL, decode_responses=True))
    filled = 0

    for i, (lot_id, anno_id) in enumerate(rows, 1):
        try:
            code = fetch_lot_enstru_code(anno_id, lot_id, session=http)
        except Exception as e:
            log.warning("[%d/%d] lot %s: ошибка запроса: %s", i, total, lot_id, e)
            continue

        if not code:
            log.info("[%d/%d] lot %s: код не найден", i, total, lot_id)
            continue

        with SessionLocal() as s:
            lot = s.get(Lot, lot_id)
            if lot is not None and lot.enstru_code is None:
                lot.enstru_code = code
                s.commit()
                filled += 1
        log.info("[%d/%d] lot %s: %s", i, total, lot_id, code)

    log.info("ГОТОВО: заполнено %d кодов из %d", filled, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
