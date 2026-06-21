"""Точный тайминг подачи: ждать до `open_at` и выстрелить с минимальной задержкой.

Гонка решается миллисекундами, а `time.sleep()` просыпается «примерно» — поэтому
двухфазное ожидание: спим до `target - busy_window`, затем busy-loop крутим до
самого момента. Busy-loop жжёт CPU, но всего ~0.3 с на подачу — приемлемо за
детерминированную точность пробуждения.

О времени сервера: истина — часы goszakup. Хост обязан быть синхронизирован по
NTP (см. план §3). Заголовок `Date` ответа goszakup даёт грубую (1 с) сверку
смещения для санити-чека, не для точной синхронизации. `clock_offset`
(server − local, сек) можно подать, если измерили смещение точнее.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime


def server_offset_from_date(date_header: str, local_recv: datetime) -> float:
    """Грубое смещение (server − local) в секундах из заголовка `Date` ответа.

    Точность ~1 с (Date округлён до секунд) — только для санити-чека NTP, не для
    суб-секундной синхронизации.
    """
    server = parsedate_to_datetime(date_header)
    if server.tzinfo is None:
        server = server.replace(tzinfo=UTC)
    return (server - local_recv).total_seconds()


def wait_until(
    target: datetime,
    *,
    clock_offset: float = 0.0,
    busy_window: float = 0.3,
    lead: float = 0.0,
) -> datetime:
    """Блокироваться до `target` (в часах сервера), вернуть фактический момент.

    `clock_offset` — server−local (сек): целимся в `target - clock_offset` по
    локальным часам. `lead` — преднамеренное упреждение (сек): стрелять чуть
    раньше расчётного нуля (компенсация сетевой задержки до сервера). `busy_window`
    — сколько финальных секунд крутим busy-loop вместо sleep.
    """
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)

    def remaining() -> float:
        now = datetime.now(UTC)
        # переводим «сейчас» в часы сервера, целимся с упреждением lead
        return (target - now).total_seconds() - clock_offset - lead

    coarse = remaining() - busy_window
    if coarse > 0:
        time.sleep(coarse)
    while remaining() > 0:
        pass  # busy-wait финальное окно ради точности пробуждения
    return datetime.now(UTC)


def deadline_guard(close_at: datetime | None, *, margin: float = 30.0) -> bool:
    """True, если подавать ещё безопасно (до `close_at` минус запас). None = без дедлайна."""
    if close_at is None:
        return True
    if close_at.tzinfo is None:
        close_at = close_at.replace(tzinfo=UTC)
    return datetime.now(UTC) < close_at - timedelta(seconds=margin)
