"""Точный тайминг «выстрела» к open_at. Копия логики goszakup.autosubmit.timing.

Двухфазное ожидание: sleep до `target - busy_window`, затем busy-loop до самого
момента (точность пробуждения). Хост обязан быть синхронизирован по NTP.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime


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
