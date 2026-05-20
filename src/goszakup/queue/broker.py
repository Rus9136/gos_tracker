"""Dramatiq broker — единая точка для всех actor'ов проекта.

URL берётся из `GZ_REDIS_URL` (.env). Дефолт `redis://localhost:6379/0`
работает только если на машине поднят redis-server (или для тестов через
fakeredis — там broker подменяется в conftest).

`broker` импортируется до любых `@dramatiq.actor` — поэтому декларируем
здесь, до actors.py. Dramatiq использует глобальный singleton.

ВАЖНО: импортируем `..config` ПЕРВЫМ — он, как side-effect, вызывает
`load_dotenv()`. Иначе `os.environ.get("GZ_REDIS_URL", ...)` ниже падает
в дефолт, потому что .env ещё не прочитан (queue/__init__.py тащит broker
раньше, чем actors.py успевает импортировать config).
"""

from __future__ import annotations

import logging
import os

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AgeLimit,
    Callbacks,
    CurrentMessage,
    Pipelines,
    Retries,
    ShutdownNotifications,
    TimeLimit,
)

from .. import config  # noqa: F401 — side-effect: load_dotenv()

log = logging.getLogger(__name__)

REDIS_URL = os.environ.get("GZ_REDIS_URL", "redis://localhost:6379/0")

# Без middleware Prometheus — нам пока хватает retries + time-limit.
# AgeLimit добавит метку «не выполнять, если в очереди >24ч» — нужно
# на случай, если worker лежит и сообщения не разбираются: лучше дропнуть
# устаревшие, чем хлынуть лавиной после восстановления.
_DEFAULT_MIDDLEWARE = [
    AgeLimit(max_age=24 * 60 * 60 * 1000),  # 24h, ms
    TimeLimit(time_limit=10 * 60 * 1000),   # 10 min на actor
    ShutdownNotifications(),
    Callbacks(),
    Pipelines(),
    Retries(min_backoff=5_000, max_backoff=10 * 60 * 1000, max_retries=3),
    CurrentMessage(),
]

broker = RedisBroker(url=REDIS_URL, middleware=_DEFAULT_MIDDLEWARE)
dramatiq.set_broker(broker)

log.info("dramatiq broker ready: %s", REDIS_URL)
