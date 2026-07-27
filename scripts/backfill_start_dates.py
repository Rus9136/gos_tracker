"""Бэкофилл `Announcement.application_start` по уже накопленным объявлениям.

Поле появилось вместе с `TrdBuy.startDate` (правило #21) — у объявлений,
добытых раньше, начало приёма заявок пустое, и автоподаче (правило #19) неоткуда
взять `open_at`. GraphQL-фильтр `TrdBuy.id` принимает массив, поэтому идём
пачками по BATCH id вместо запроса на объявление: ~200 объявлений за один вызов.

Резюмируемо: берём только записи с пустым `application_start`, коммитим после
каждой пачки. Порядок — от свежих id к старым: у свежих объявлений приём заявок
ещё не начался, ради них всё и затевалось.

Работает ТОЛЬКО по API (без токена смысла нет: HTML-путь — это +1 запрос на
объявление с Crawl-delay 5с, для бэкофилла на десятки тысяч записей неприемлемо).

Usage:
    python -m scripts.backfill_start_dates            # только актуальные лоты
    python -m scripts.backfill_start_dates --all      # вообще все объявления
"""

import logging
import sys

from sqlalchemy import distinct, select

from goszakup.api.client import OwsApiError, OwsClient
from goszakup.api.mapping import almaty_to_utc
from goszakup.config import OWS_TOKEN
from goszakup.db.engine import SessionLocal
from goszakup.db.models import Announcement, Lot

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("backfill_start_dates")
log.setLevel(logging.INFO)

BATCH = 200

QUERY = """
query($f: TrdBuyFiltersInput, $limit: Int) {
  TrdBuy(filter: $f, limit: $limit) {
    id
    startDate
  }
}
"""


def main() -> int:
    if not OWS_TOKEN:
        log.error("нет GZ_OWS_TOKEN — бэкофилл возможен только по API")
        return 1

    only_actual = "--all" not in sys.argv[1:]

    with SessionLocal() as session:
        q = select(distinct(Announcement.id)).where(
            Announcement.application_start.is_(None)
        )
        if only_actual:
            q = q.join(Lot, Lot.announcement_id == Announcement.id).where(
                Lot.is_actual.is_(True)
            )
        anno_ids = list(session.scalars(q.order_by(Announcement.id.desc())))

    log.info(
        "объявлений без application_start: %d (%s)",
        len(anno_ids),
        "только актуальные" if only_actual else "все",
    )
    if not anno_ids:
        return 0

    client = OwsClient()
    filled = missing = 0
    for i in range(0, len(anno_ids), BATCH):
        chunk = anno_ids[i : i + BATCH]
        try:
            data, _ = client.graphql(
                QUERY, {"f": {"id": chunk}, "limit": len(chunk)}
            )
        except OwsApiError as e:
            # Пачка могла не пройти из-за одного битого id — не роняем весь
            # прогон, следующий запуск доберёт её (скрипт резюмируемый).
            log.warning("пачка %d-%d провалена: %s", i, i + len(chunk), e)
            continue

        with SessionLocal() as session:
            for tb in data.get("TrdBuy") or []:
                start = almaty_to_utc(tb.get("startDate"))
                if start is None:
                    missing += 1
                    continue
                anno = session.get(Announcement, int(tb["id"]))
                if anno is None:
                    continue
                anno.application_start = start
                filled += 1
            session.commit()
        log.info(
            "обработано %d/%d — заполнено %d, без startDate %d",
            min(i + BATCH, len(anno_ids)), len(anno_ids), filled, missing,
        )

    log.info("готово: заполнено %d, у %d объявлений startDate пуст в API", filled, missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
