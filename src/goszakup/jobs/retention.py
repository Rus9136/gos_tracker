"""Retention скачанных ТЗ: не давать data/docs/ расти вечно.

Файлы документов копились без ограничения (Гейт 2): ТЗ прошедших лотов никогда
не удалялись, а на 800K лотов/год это неограниченный рост диска. Удаляем файлы
для документов старше N дней у объявлений БЕЗ актуальных лотов. Строку Document
оставляем (sha256/provenance), обнуляем local_path — при нужде файл перекачается
кнопкой «Загрузить документы». Health-сторож отдельно алертит про низкий диск.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Document, Lot

log = logging.getLogger(__name__)

RETENTION_DAYS = int(os.environ.get("GZ_DOCS_RETENTION_DAYS", "90"))
DEFAULT_LIMIT = 500


def cleanup_old_documents(
    session: Session, older_than_days: int = RETENTION_DAYS, limit: int = DEFAULT_LIMIT
) -> tuple[int, int]:
    """Удалить файлы ТЗ старше N дней у объявлений без актуальных лотов.

    Возвращает (удалено_файлов, освобождено_байт). local_path обнуляется, строка
    Document остаётся.
    """
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    active_annos = (
        select(Lot.announcement_id)
        .where(Lot.is_actual.is_(True), Lot.announcement_id.is_not(None))
        .distinct()
    )
    docs = session.scalars(
        select(Document)
        .where(
            Document.local_path.is_not(None),
            Document.downloaded_at < cutoff,
            Document.announcement_id.not_in(active_annos),
        )
        .limit(limit)
    ).all()

    removed = 0
    freed = 0
    for d in docs:
        path = Path(d.local_path)
        try:
            if path.exists():
                freed += path.stat().st_size
                path.unlink()
            d.local_path = None
            removed += 1
        except OSError as e:
            log.warning("retention: не удалил %s: %s", d.local_path, e)
    session.commit()
    if removed:
        log.info(
            "retention: удалено %d файлов ТЗ, освобождено %.1f МБ", removed, freed / 1e6
        )
    return removed, freed
