"""Кросс-процессный rate-limit для HTTP-запросов к goszakup.

`ThrottledSession` (scraper/http.py) использовал `threading.Lock`, который
работает только внутри одного процесса. С dramatiq и несколькими worker'ами
этого мало — нужен общий координирующий механизм.

Реализация: distributed mutex поверх Redis-ключа с TTL=delay-секунд.
- Worker делает `SET key value NX EX delay`.
- Если ключ удалось установить (NX вернул OK) — у этого worker'а есть
  «слот» на следующие `delay` секунд, никто другой запроса не сделает.
- Если ключ был — смотрим TTL, спим, ретраим.

Эта схема даёт **глобально** не более 1 запроса в `delay` секунд, как
обещает goszakup robots.txt.

Fallback: если `GZ_REDIS_URL` не задан или Redis недоступен,
`RedisThrottledSession` падает в `ThrottledSession` (in-process Lock) —
полезно для unit-тестов и CLI smoke-вызовов.
"""

from __future__ import annotations

import logging
import time

import requests

from ..config import CRAWL_DELAY, HTTP_HEADERS
from ..scraper.http import ThrottledSession

log = logging.getLogger(__name__)

# Один ключ на весь проект. Если когда-то понадобится несколько источников
# с независимыми лимитами — параметризуем именем.
LIMIT_KEY = "goszakup:rate_limit"


class RedisThrottledSession:
    """Drop-in замена ThrottledSession, координирующая worker'ов через Redis."""

    def __init__(self, redis_client, delay: float = CRAWL_DELAY) -> None:
        self.redis = redis_client
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(HTTP_HEADERS)

    def _wait_for_slot(self) -> None:
        # Цикл: пытаемся занять слот; если занят — спим оставшийся TTL.
        # PTTL даёт миллисекунды, точнее чем TTL. На случай гонки берём
        # max(0.05, ...) — иначе можем закрутиться в spin при ttl=0.
        while True:
            if self.redis.set(LIMIT_KEY, "1", nx=True, ex=int(self.delay)):
                return
            ttl_ms = self.redis.pttl(LIMIT_KEY)
            sleep_s = max(0.05, (ttl_ms or 100) / 1000)
            time.sleep(sleep_s)

    def get(self, url: str, **kwargs) -> requests.Response:
        self._wait_for_slot()
        kwargs.setdefault("timeout", 30)
        return self.session.get(url, **kwargs)


def make_http_session(redis_client=None, delay: float = CRAWL_DELAY):
    """Фабрика: если есть Redis — кросс-процессная, иначе fallback на in-process.

    Использовать вместо прямого `ThrottledSession()` в actor'ах. Гарантирует,
    что unit-тесты, которые не поднимают Redis, не падают на импорте.
    """
    if redis_client is None:
        return ThrottledSession(delay=delay)
    return RedisThrottledSession(redis_client, delay=delay)
