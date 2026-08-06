"""Уведомления о новых пунктах годового плана (правило #26).

Отличие от матчинга лотов (правило #17): у пункта плана нет ТЗ — только
название, характеристика и код ЕНС ТРУ, поэтому звать LLM не на чем.
Отбор идёт ТОЛЬКО пре-фильтром запроса (`prefilter.py`), и запрос без
пре-фильтра не уведомляет ни о чём — иначе пользователю прилетал бы весь
план его вертикали.

Два предохранителя от лавины:

- `MAX_AGE_DAYS` — уведомляем лишь о пунктах, СОЗДАННЫХ в источнике недавно.
  Стартовый залив года (2.6 млн пунктов) молчит, потому что `created_at`
  у них старый; отсечка по `first_seen` этого не дала бы.
- строка `PlanNotification` пишется в любом случае — и когда уведомление
  ушло, и когда не ушло (выключено, нет chat_id): иначе один и тот же пункт
  разбирался бы каждым прогоном заново.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import PlanNotification, PlanPoint, User, UserQuery
from ..notify.render import build_plan_message
from ..notify.telegram import send_message
from ..prefilter import plan_passes_prefilter, plan_prefilter_conditions
from ..scope import plan_in_scope_of, plan_scope_conditions_of, user_scope
from .plans import PLANNED_STATUSES

log = logging.getLogger(__name__)

MAX_AGE_DAYS = 3
DEFAULT_LIMIT = 200


@dataclass
class PlanNotifyStats:
    candidates: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


def notify_new_plan_points(
    session: Session,
    *,
    limit: int = DEFAULT_LIMIT,
    max_age_days: int = MAX_AGE_DAYS,
    now: datetime | None = None,
    sender=send_message,
) -> PlanNotifyStats:
    stats = PlanNotifyStats()
    now = now or datetime.now(UTC)
    since = now - timedelta(days=max_age_days)

    queries = session.scalars(
        select(UserQuery).where(
            UserQuery.active.is_(True), UserQuery.compiled_filters.is_not(None)
        )
    ).all()
    if not queries:
        return stats

    for query in queries:
        user = session.get(User, query.user_id)
        # Тумблер плана — отдельный от notify_telegram: это уведомления не о
        # лотах, а о намерениях, поток другой (правило #26).
        if user is None or not user.notify_plan:
            continue
        scope = user_scope(user)
        pf = query.compiled_filters
        # Пустой пре-фильтр отсекаем и здесь: на Postgres в jsonb-колонке
        # может лежать не SQL NULL, а json 'null', который `is_not(None)`
        # пропускает — и запрос молча уведомлял бы обо всём подряд.
        if not pf:
            continue

        already = select(PlanNotification.plan_root_id).where(
            PlanNotification.user_query_id == query.id
        )
        candidates = session.scalars(
            select(PlanPoint)
            .where(
                PlanPoint.is_active.is_(True),
                PlanPoint.status_id.in_(PLANNED_STATUSES),
                PlanPoint.created_at >= since,
                PlanPoint.root_id.not_in(already),
                *plan_prefilter_conditions(pf),
                # Пересечение со scope владельца — то же требование, что у
                # watchlist (правило #25): пре-фильтром нельзя заказать
                # уведомление о рынке, которого он всё равно не увидит.
                *plan_scope_conditions_of(scope),
            )
            .order_by(PlanPoint.amount.desc())
            .limit(limit)
        ).all()

        for point in candidates:
            # SQL-условия пре-фильтра — надмножество (keywords в них не
            # выражаются), решает Python-предикат.
            if not plan_passes_prefilter(pf, point) or not plan_in_scope_of(
                scope, point
            ):
                continue
            stats.candidates += 1
            error = None
            if not user.telegram_chat_id:
                error = "нет telegram_chat_id"
            else:
                ok, err = sender(user.telegram_chat_id, build_plan_message(query, point))
                if not ok:
                    error = err or "ошибка отправки"
            session.add(
                PlanNotification(
                    user_query_id=query.id, plan_root_id=point.root_id, error=error
                )
            )
            if error:
                stats.skipped += 1
                log.warning(
                    "plan-notify: пункт %s не отправлен (%s)", point.root_id, error
                )
            else:
                stats.sent += 1
        session.commit()
    return stats
