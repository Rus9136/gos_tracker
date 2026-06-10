"""Backfill матчинга: прогнать существующий запрос по уже накопленным лотам.

Forward-поток (новые лоты) идёт через analyze_actor → enqueue_matches_for_lot.
Но при СОЗДАНИИ или ПРАВКЕ запроса нужно один раз пройтись по актуальным
лотам, которые уже в БД. Этим занимается backfill_query.

По умолчанию ставит задачи в очередь (нужен запущенный воркер). Режим
sync=True считает матчи прямо здесь (для тестов / прогона без Redis).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..classify.matcher import match_and_save
from ..db.engine import SessionLocal
from ..db.models import Lot, User, UserQuery
from ..scope import scope_conditions

log = logging.getLogger(__name__)

# Защитный потолок: backfill по одному запросу не должен внезапно зарядить
# десятки тысяч LLM-вызовов. Поднимается осознанно через аргумент.
DEFAULT_LIMIT = 2000


def _actual_lots_in_scope(session: Session, user: User | None, limit: int) -> list[Lot]:
    stmt = (
        select(Lot)
        .options(selectinload(Lot.analysis), selectinload(Lot.customer))
        .where(Lot.is_actual.is_(True), *scope_conditions(user))
        .order_by(Lot.first_seen.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def backfill_query(query_id: int, *, limit: int = DEFAULT_LIMIT, sync: bool = False) -> int:
    """Сопоставить запрос с актуальными лотами в scope владельца.

    Возвращает число обработанных/поставленных лотов.
    """
    with SessionLocal() as session:
        query = session.get(UserQuery, query_id)
        if query is None:
            log.warning("backfill: query %s не найден", query_id)
            return 0
        # user может отсутствовать только на dev: GZ_NO_AUTH-админ имеет id=0,
        # которого нет в users (SQLite не enforce'ит FK). None → scope без
        # ограничений (scope_conditions(None) == []), как у админа.
        user = session.get(User, query.user_id)

        lots = _actual_lots_in_scope(session, user, limit)
        if len(lots) >= limit:
            log.warning(
                "backfill query %s: достигнут лимит %d лотов — часть могла "
                "остаться без матча; увеличьте limit при необходимости.",
                query_id, limit,
            )

        if sync:
            n = 0
            for lot in lots:
                if match_and_save(session, query, lot):
                    session.commit()
                    n += 1
            log.info("backfill (sync) query %s: %d матчей посчитано", query_id, n)
            return n

        # Async: ставим в очередь (импорт здесь, чтобы sync-режим работал без
        # живого Redis/брокера).
        from ..queue.matching import enqueue_matches_for_query

        return enqueue_matches_for_query(query, lots)
