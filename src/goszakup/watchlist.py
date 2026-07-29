"""Watchlist — какие лоты заслуживают дорогих стадий (документы, LLM).

Отдельно от scope.py намеренно: scope — read-time изоляция пользователя
(правило #15), watchlist — экономика пайплайна. Смешивать концепции нельзя.

Лот попадает в watchlist, если выполняется хотя бы одно из:
  (а) его вертикаль перечислена в `categories` активного пользователя;
  (б) он проходит пре-фильтр активного `UserQuery` И виден его владельцу
      по scope.

ВНИМАНИЕ, инверсия семантики. Пустой `User.categories` в scope означает
«вижу все вертикали» (правило #15), а здесь — «не расширяю watchlist».
Иначе один админ с NULL-scope затянул бы в LLM весь рынок (≈$105/мес по
расчёту SAAS_PIVOT_PLAN.md). Это не баг и не забытая ветка.

Пересечение (б) со scope владельца тоже намеренное: без него клиент с
`categories=['it']` мог бы пре-фильтром заказать анализ всей медицины,
которую всё равно никогда не увидит. Практическое следствие — пре-фильтр
реально расширяет watchlist только у пользователя без вертикального
ограничения (у остальных вертикальный терм уже всё покрывает).

`should_analyze` авторитетен; `watchlist_conditions` — его SQL-зеркало и
заведомое НАДМНОЖЕСТВО (см. prefilter.py про keywords). Пустые правила дают
`false()`, никогда не `None`: `select(...).where(None)` означал бы «весь
рынок» — ровно наоборот.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

from sqlalchemy import and_, false, or_, select, true
from sqlalchemy.orm import Session

from .db.models import Lot, User, UserQuery
from .prefilter import lot_passes_prefilter, prefilter_conditions
from .scope import Scope, lot_in_scope_of, scope_conditions_of, user_scope

log = logging.getLogger(__name__)

# Правила меняются редко (правка scope/запроса), а спрашивают их на каждом
# детализируемом лоте. Процессный кеш вместо Redis: вызовов ~1-2 тыс./сутки,
# а sync-пути CLI (reanalyze, run-preset) живут вообще без брокера.
CACHE_TTL = 0.0 if os.environ.get("GZ_TEST_MODE") else 60.0

_lock = threading.Lock()
_cached: tuple[float, list[WatchRule]] | None = None


@dataclass(frozen=True)
class WatchRule:
    """Одно основание для анализа. Плоские данные, НЕ ORM — кеш переживает
    Session, а детач-объект `User` дал бы DetachedInstanceError."""

    scope: Scope
    prefilter: dict | None = None

    def matches(self, lot: Lot) -> bool:
        return lot_in_scope_of(self.scope, lot) and lot_passes_prefilter(
            self.prefilter, lot
        )

    def conditions(self) -> list:
        return scope_conditions_of(self.scope) + prefilter_conditions(self.prefilter)


def _build_rules(session: Session) -> list[WatchRule]:
    rules: list[WatchRule] = []

    # (а) вертикали подписчиков. Регионы и min_amount владельца сознательно
    # игнорируем: анализ лота — общий актив (идемпотентен, переиспользуется
    # всеми), а регионы у клиентов разные — сужать по ним нечего.
    for categories in session.scalars(
        select(User.categories).where(User.is_active.is_(True))
    ):
        if categories:
            rules.append(WatchRule(scope=Scope(categories=frozenset(categories))))

    # (б) пре-фильтры запросов ∩ scope владельца. outerjoin — на dev
    # (GZ_NO_AUTH) запросы принадлежат синтетическому админу id=0, которого
    # нет в users; user=None → scope «видит всё», как у админа.
    rows = session.execute(
        select(UserQuery.compiled_filters, User)
        .outerjoin(User, UserQuery.user_id == User.id)
        .where(UserQuery.active.is_(True))
    ).all()
    for compiled_filters, user in rows:
        if not compiled_filters:
            continue  # запрос без пре-фильтра анализ не заказывает
        if user is not None and not user.is_active:
            continue
        rules.append(WatchRule(scope=user_scope(user), prefilter=compiled_filters))

    if not rules:
        # Не ошибка (пустая БД на dev), но на проде это значит «документы и
        # LLM выключены для всего рынка» — health-check поднимет тревогу.
        log.warning("watchlist пуст: ни одной вертикали и ни одного пре-фильтра")
    return rules


def watchlist_rules(session: Session) -> list[WatchRule]:
    global _cached
    now = time.monotonic()
    with _lock:
        if _cached is not None and now - _cached[0] < CACHE_TTL:
            return _cached[1]
    rules = _build_rules(session)
    with _lock:
        _cached = (now, rules)
    return rules


def invalidate_watchlist_cache() -> None:
    """Звать после правки scope пользователя или пре-фильтра запроса.

    Инвалидация процессная: у воркера свой кеш, он подхватит изменение сам
    в течение CACHE_TTL.
    """
    global _cached
    with _lock:
        _cached = None


def should_analyze(session: Session, lot: Lot) -> bool:
    return any(rule.matches(lot) for rule in watchlist_rules(session))


def watchlist_conditions(session: Session):
    """SQL-надмножество предиката — для отбора кандидатов (cli reanalyze,
    catch-up). Результат обязательно дофильтровывать `should_analyze`."""
    clauses = []
    for rule in watchlist_rules(session):
        conds = rule.conditions()
        clauses.append(and_(*conds) if conds else true())
    return or_(*clauses) if clauses else false()
