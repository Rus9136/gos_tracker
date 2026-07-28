"""Read-time scope пользователя — единый источник правды.

Лоты в БД общие на всех (скрейпинг глобальный), но каждый пользователь видит
только лоты в своём scope: регионы (kato), вертикали, мин. сумма. Админ
(и dev-аноним) видит всё.

Раньше эта логика жила только в web/app.py. Вынесена сюда, потому что её же
использует matcher-fan-out (queue/matching.py, jobs/match.py) как дешёвый
pre-filter перед LLM. Пустой regions/categories = «без ограничения».
"""

from __future__ import annotations

from .db.models import Lot, User


def scope_conditions(user: User | None) -> list:
    """SQLAlchemy-условия для WHERE: ограничить выборку лотов scope'ом."""
    if user is None or user.is_admin:
        return []
    conds = []
    if user.regions:
        conds.append(Lot.kato.in_(user.regions))
    if user.categories:
        conds.append(Lot.category.in_(user.categories))
    if user.min_amount:
        conds.append(Lot.plan_amount >= user.min_amount)
    return conds


def lot_in_scope(lot: Lot, user: User | None) -> bool:
    """Тот же scope, что и `scope_conditions`, но в Python — для проверки
    одного лота (drill-down по ссылке, pre-filter перед матчингом)."""
    if user is None or user.is_admin:
        return True
    if user.regions and lot.kato not in user.regions:
        return False
    if user.categories and lot.category not in user.categories:
        return False
    if user.min_amount and (lot.plan_amount is None or lot.plan_amount < user.min_amount):
        return False
    return True
