"""Догон watchlist: разобрать лоты, попавшие в него задним числом.

Watchlist — функция подписок (см. watchlist.py), а значит расширяется в
любой момент: новый клиент с вертикалью «медицина», новая вертикаль у
существующего, новый пре-фильтр запроса. Лоты, уже лежащие в БД, при этом
не догоняются ничем:

- `run_preset.execute_search` берёт детали только для НОВЫХ лотов и
  сменивших статус — старый лот в фазу 2 не попадёт больше никогда;
- `jobs/match.backfill_query` ставит только `match_actor`, анализ не заказывает.

Поэтому нужен явный проход: найти актуальные watchlist-лоты без анализа и
отправить их объявления в `detail_actor` (он скачает ТЗ и поставит LLM).
Потолок жёсткий — подписка на широкую вертикаль иначе разом зарядила бы
десятки тысяч скачиваний.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db.engine import SessionLocal, init_db
from ..db.models import Lot, LotAnalysis, ScrapeRun
from ..watchlist import should_analyze, watchlist_conditions

log = logging.getLogger(__name__)

NOTE_PREFIX = "watchlist-catchup"

# Потолок по ОБЪЯВЛЕНИЯМ (не лотам): каждое — это поход за деталями и ТЗ.
DEFAULT_LIMIT = 300


def announcements_to_catchup(session: Session, limit: int) -> list[int]:
    """Объявления с актуальными watchlist-лотами, у которых нет анализа.

    Отсутствие строки в `lot_analyses` — единственный надёжный признак «не
    разбирали»: при ошибке LLM запись намеренно не создаётся (правило #20),
    а устаревшую версию анализа догоняет `cli reanalyze` (правило #8).
    """
    stmt = (
        select(Lot)
        .options(selectinload(Lot.analysis))
        .outerjoin(LotAnalysis, LotAnalysis.lot_id == Lot.id)
        .where(
            Lot.is_actual.is_(True),
            LotAnalysis.id.is_(None),
            # SQL — надмножество (keywords пре-фильтров не выражаются),
            # финальный гейт should_analyze ниже.
            watchlist_conditions(session),
        )
        .order_by(Lot.first_seen.desc())
    )
    anno_ids: list[int] = []
    seen: set[int] = set()
    for lot in session.scalars(stmt):
        if lot.announcement_id in seen or not should_analyze(session, lot):
            continue
        seen.add(lot.announcement_id)
        anno_ids.append(lot.announcement_id)
        if len(anno_ids) >= limit:
            log.warning(
                "watchlist-catchup: достигнут потолок %d объявлений — "
                "остаток догонит следующий запуск",
                limit,
            )
            break
    return anno_ids


def run_catchup(*, limit: int = DEFAULT_LIMIT, dry_run: bool = False) -> int:
    """Поставить детали по «догоняемым» объявлениям. Возвращает их число."""
    init_db()
    from ..queue.actors import _redis_client, _set_pending, detail_actor

    with SessionLocal() as session:
        anno_ids = announcements_to_catchup(session, limit)
        if not anno_ids or dry_run:
            log.info("watchlist-catchup: %d объявлений%s", len(anno_ids),
                     " (dry-run)" if dry_run else "")
            return len(anno_ids)

        run = ScrapeRun(preset_id=None, note=f"{NOTE_PREFIX}: {len(anno_ids)} объявл.")
        session.add(run)
        session.commit()
        run_id = run.id

    r = _redis_client()
    if r is not None:
        _set_pending(r, run_id, len(anno_ids))
    for anno_id in anno_ids:
        detail_actor.send(anno_id, run_id, detail_scope="watchlist")
    log.info("watchlist-catchup run #%d: %d объявлений", run_id, len(anno_ids))
    return len(anno_ids)
