"""Точный тайминг «выстрела» к open_at. Копия логики goszakup.autosubmit.timing.

Двухфазное ожидание: sleep до `target - busy_window`, затем busy-loop до самого
момента (точность пробуждения). Хост обязан быть синхронизирован по NTP.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta


def wait_until(
    target: datetime,
    *,
    clock_offset: float = 0.0,
    busy_window: float = 0.3,
    lead: float = 0.0,
) -> datetime:
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)

    def remaining() -> float:
        now = datetime.now(UTC)
        return (target - now).total_seconds() - clock_offset - lead

    coarse = remaining() - busy_window
    if coarse > 0:
        time.sleep(coarse)
    while remaining() > 0:
        pass
    return datetime.now(UTC)


def deadline_guard(close_at: datetime | None, *, margin: float = 30.0) -> bool:
    """True, если подавать ещё безопасно (до `close_at` минус запас). None = без дедлайна.

    Зеркало goszakup.autosubmit.timing.deadline_guard — агент не стреляет после
    close_at по локальным часам (безопасная деградация, а не подача «в молоко»).
    """
    if close_at is None:
        return True
    if close_at.tzinfo is None:
        close_at = close_at.replace(tzinfo=UTC)
    return datetime.now(UTC) < close_at - timedelta(seconds=margin)
