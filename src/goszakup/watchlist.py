"""Watchlist — какие лоты заслуживают дорогих стадий (документы, LLM).

Отдельно от scope.py намеренно: scope — read-time изоляция пользователя
(правило #15), watchlist — экономика пайплайна. Смешивать концепции нельзя.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .db.models import Lot


def should_analyze(session: Session, lot: Lot) -> bool:
    """Заглушка фазы A: watchlist = вертикаль IT (поведение бит-в-бит прежнее).

    Фаза C заменит на f(вертикали активных пользователей, пре-фильтры
    активных UserQuery) с Redis-кешем — сигнатура с session заложена под
    это заранее. SQL-зеркало предиката (отбор кандидатов в cli reanalyze)
    пока `Lot.category == "it"` — при замене менять синхронно.
    """
    return lot.category == "it"
