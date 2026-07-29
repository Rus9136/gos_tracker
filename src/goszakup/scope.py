"""Read-time scope пользователя — единый источник правды.

Лоты в БД общие на всех (скрейпинг глобальный), но каждый пользователь видит
только лоты в своём scope: регионы (kato), вертикали, мин. сумма. Админ
(и dev-аноним) видит всё.

Раньше эта логика жила только в web/app.py. Вынесена сюда, потому что её же
использует matcher-fan-out (queue/matching.py, jobs/match.py) как дешёвый
pre-filter перед LLM. Пустой regions/categories = «без ограничения».

`Scope` — те же правила, но на плоских данных без ORM: watchlist кеширует
их между сессиями, а детач-объект `User` там дал бы DetachedInstanceError.
`scope_conditions`/`lot_in_scope` оставлены тонкими обёртками, чтобы
call-sites не переписывать и чтобы логика не раздваивалась.
"""

from __future__ import annotations

from dataclasses import dataclass

from .db.models import Lot, User


@dataclass(frozen=True)
class Scope:
    regions: frozenset[str] | None = None
    categories: frozenset[str] | None = None
    min_amount: int | None = None


# «Видит всё» — админ, dev-аноним и пользователь без ограничений.
UNRESTRICTED = Scope()


def user_scope(user: User | None) -> Scope:
    if user is None or user.is_admin:
        return UNRESTRICTED
    return Scope(
        regions=frozenset(user.regions) if user.regions else None,
        categories=frozenset(user.categories) if user.categories else None,
        min_amount=user.min_amount or None,
    )


def scope_conditions_of(scope: Scope) -> list:
    conds = []
    if scope.regions:
        conds.append(Lot.kato.in_(scope.regions))
    if scope.categories:
        conds.append(Lot.category.in_(scope.categories))
    if scope.min_amount:
        conds.append(Lot.plan_amount >= scope.min_amount)
    return conds


def lot_in_scope_of(scope: Scope, lot: Lot) -> bool:
    if scope.regions and lot.kato not in scope.regions:
        return False
    if scope.categories and lot.category not in scope.categories:
        return False
    if scope.min_amount and (
        lot.plan_amount is None or lot.plan_amount < scope.min_amount
    ):
        return False
    return True


def scope_conditions(user: User | None) -> list:
    """SQLAlchemy-условия для WHERE: ограничить выборку лотов scope'ом."""
    return scope_conditions_of(user_scope(user))


def lot_in_scope(lot: Lot, user: User | None) -> bool:
    """Тот же scope, что и `scope_conditions`, но в Python — для проверки
    одного лота (drill-down по ссылке, pre-filter перед матчингом)."""
    return lot_in_scope_of(user_scope(user), lot)
