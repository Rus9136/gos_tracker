"""P0/P1-5: deadline_guard реализован и работает на обеих сторонах.

До фикса функция была только в goszakup.autosubmit.timing и нигде не вызывалась,
а в agent/timing.py отсутствовала — защита «не подавать после close_at» была
мертва. Здесь фиксируем поведение самой функции в обоих модулях.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent.timing import deadline_guard as agent_guard
from goszakup.autosubmit.timing import deadline_guard as linux_guard


@pytest.mark.parametrize("guard", [linux_guard, agent_guard])
def test_none_deadline_is_safe(guard):
    assert guard(None) is True


@pytest.mark.parametrize("guard", [linux_guard, agent_guard])
def test_future_deadline_is_safe(guard):
    assert guard(datetime.now(UTC) + timedelta(minutes=10)) is True


@pytest.mark.parametrize("guard", [linux_guard, agent_guard])
def test_past_deadline_is_unsafe(guard):
    assert guard(datetime.now(UTC) - timedelta(seconds=1)) is False


@pytest.mark.parametrize("guard", [linux_guard, agent_guard])
def test_within_margin_is_unsafe(guard):
    # До дедлайна ещё 10с, но margin=30с — уже поздно стартовать подачу.
    assert guard(datetime.now(UTC) + timedelta(seconds=10), margin=30.0) is False
