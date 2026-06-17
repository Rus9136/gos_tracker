"""Dramatiq-слой матчинга: actor + fan-out предпочтений пользователя на лоты.

Поток:
  analyze_actor (лот разобран) ──> enqueue_matches_for_lot()
        └─ для каждого active UserQuery, чей владелец видит лот по scope,
           ставит match_actor(query_id, lot_id).

  Правка/создание запроса ──> enqueue_matches_for_query() (backfill по
        актуальным лотам в scope, см. jobs/match.py).

Pre-filter по scope (goszakup.scope.lot_in_scope) — ДО постановки в очередь:
не зовём LLM на лотах, которые пользователь и так не видит. Идемпотентность
самой пары (query, lot) — внутри match_and_save (по query_version +
matcher_version), поэтому повторный enqueue безопасен.

Actor живёт в отдельной очереди `goszakup_matching` (префикс — конвенция
проекта, dev-Redis может быть общим) — чтобы можно было крутить отдельного
воркера и не конкурировать с listing/detail/llm. Регистрируется при импорте
из queue/actors.py (воркер поднимается как `dramatiq goszakup.queue.actors`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import dramatiq
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..classify.matcher import match_and_save
from ..db.engine import SessionLocal
from ..db.models import Lot, User, UserLotMatch, UserQuery
from ..scope import lot_in_scope
from .broker import broker  # noqa: F401 — импорт broker до actor'а обязателен

log = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="goszakup_matching",
    max_retries=2,
    min_backoff=10_000,
    max_backoff=60_000,
    time_limit=2 * 60 * 1000,
)
def match_actor(user_query_id: int, lot_id: int, notify: bool = False) -> None:
    """Посчитать матч одной пары (запрос × лот) и сохранить в UserLotMatch.

    `notify=True` (forward-поток: новый/переанализированный лот) ставит
    Telegram-уведомление, если матч положительный и ещё не отправлялся.
    Backfill (создание/правка запроса) зовёт с notify=False — иначе при
    первом запросе пользователю прилетела бы пачка по всем старым лотам.
    """
    with SessionLocal() as session:
        query = session.get(UserQuery, user_query_id)
        lot = session.get(Lot, lot_id)
        if query is None or not query.active or lot is None:
            return
        if match_and_save(session, query, lot):
            session.commit()

        if not notify:
            return
        # Берём текущее состояние матча и в случае идемпотентного скипа
        # (match_and_save вернул False): лот мог быть переанализирован — матч
        # уже есть, но уведомление по нему ещё не уходило (notified_at IS NULL).
        match = session.scalar(
            select(UserLotMatch).where(
                UserLotMatch.user_query_id == query.id,
                UserLotMatch.lot_id == lot.id,
            )
        )
        if match is not None and match.matched and match.notified_at is None:
            # Импорт здесь, чтобы matching.py грузился без notify-зависимостей
            # (httpx) в путях, где уведомления не нужны.
            from .notify import notify_actor

            notify_actor.send(match.id)


def enqueue_matches_for_lot(session: Session, lot: Lot) -> int:
    """Fan-out: поставить match_actor для всех active-запросов, видящих лот.

    Вызывается из analyze_actor после сохранения анализа. Возвращает число
    поставленных задач (для логов/метрик).
    """
    # outerjoin: на dev (GZ_NO_AUTH) запросы создаются от синтетического админа
    # id=0, которого нет в users (SQLite не enforce'ит FK) — inner join молча
    # выкидывал бы такие запросы. user=None → lot_in_scope даёт «видит всё».
    rows = session.execute(
        select(UserQuery, User)
        .outerjoin(User, UserQuery.user_id == User.id)
        .where(UserQuery.active.is_(True))
    ).all()

    n = 0
    for query, user in rows:
        if not lot_in_scope(lot, user):
            continue
        # notify=True: это forward-поток (лот только что проанализирован) —
        # положительный матч превратится в Telegram-уведомление.
        match_actor.send(query.id, lot.id, notify=True)
        n += 1
    if n:
        log.info("fan-out: lot %s -> %d queries", lot.id, n)
    return n


def enqueue_matches_for_query(query: UserQuery, lots: Iterable[Lot]) -> int:
    """Backfill: поставить match_actor для запроса по набору лотов.

    `lots` уже должны быть отфильтрованы по scope владельца (см. jobs/match.py).
    """
    n = 0
    for lot in lots:
        match_actor.send(query.id, lot.id)
        n += 1
    if n:
        log.info("backfill: query %s -> %d lots", query.id, n)
    return n
