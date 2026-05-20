"""Повторно скачать документы, помеченные download_error и пустым local_path.

После смены инфраструктуры (FR-IP → KZ-прокси через туннель) накопилось
~428 проваленных записей. Этот скрипт идёт по каждой и пробует ещё раз.

На успех — обновляет local_path/sha256/size/content_type/downloaded_at и
очищает download_error. На неуспех — обновляет download_error свежим
сообщением.

Использует ThrottledSession (Crawl-delay 5с) — НЕ запускать одновременно
с goszakup-daily.service (оба будут давить на goszakup лишним rate).
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import UTC, datetime

from sqlalchemy import select

from goszakup.db.engine import SessionLocal
from goszakup.db.models import Document
from goszakup.scraper.documents import download_document
from goszakup.scraper.http import ThrottledSession

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("retry_dl")
log.setLevel(logging.INFO)


def main() -> int:
    with SessionLocal() as s:
        docs = s.scalars(
            select(Document)
            .where(
                Document.download_error.is_not(None),
                Document.local_path.is_(None),
            )
            .order_by(Document.announcement_id, Document.id)
        ).all()

        total = len(docs)
        log.info("на ретрай: %d документов", total)
        if total == 0:
            return 0

        http = ThrottledSession()
        ok = 0
        fail = 0
        t0 = time.monotonic()
        for i, doc in enumerate(docs, 1):
            res = download_document(
                doc.announcement_id,
                doc.url,
                session=http,
                suggested_name=doc.name,
            )
            if res.ok:
                doc.local_path = res.local_path
                doc.sha256 = res.sha256
                doc.size = res.size
                doc.content_type = res.content_type
                doc.downloaded_at = datetime.now(UTC)
                doc.download_error = None
                ok += 1
            else:
                doc.download_error = res.error
                fail += 1
            s.commit()

            if i % 10 == 0 or i == total:
                rate = i / (time.monotonic() - t0)
                eta = (total - i) / rate if rate else 0
                log.info(
                    "[%d/%d] ok=%d fail=%d rate=%.2f/s eta=%.0fs",
                    i, total, ok, fail, rate, eta,
                )

        log.info("FINAL: ok=%d fail=%d total=%d", ok, fail, total)
        return 0


if __name__ == "__main__":
    sys.exit(main())
