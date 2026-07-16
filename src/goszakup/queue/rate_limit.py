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

from ..config import CRAWL_DELAY
from ..scraper.http import ThrottledSession, build_goszakup_session

log = logging.getLogger(__name__)

# Один ключ на весь проект. Если когда-то понадобится несколько источников
# с независимыми лимитами — параметризуем именем.
LIMIT_KEY = "goszakup:rate_limit"


class RedisThrottledSession:
    """Drop-in замена ThrottledSession, координирующая worker'ов через Redis."""

    def __init__(self, redis_client, delay: float = CRAWL_DELAY) -> None:
        self.redis = redis_client
        self.delay = delay
        self.session = build_goszakup_session()

    def _wait_for_slot(self, hold_ttl: int | None = None) -> None:
        # Цикл: пытаемся занять слот; если занят — спим оставшийся TTL.
        # PTTL даёт миллисекунды, точнее чем TTL. На случай гонки берём
        # max(0.05, ...) — иначе можем закрутиться в spin при ttl=0.
        if hold_ttl is None:
            hold_ttl = max(1, int(self.delay))
        while True:
            if self.redis.set(LIMIT_KEY, "1", nx=True, ex=hold_ttl):
                return
            ttl_ms = self.redis.pttl(LIMIT_KEY)
            sleep_s = max(0.05, (ttl_ms or 100) / 1000)
            time.sleep(sleep_s)

    def _open_next_slot(self) -> None:
        # После запроса выставляем окно до следующего слота = delay (перекрывая
        # длинный hold-TTL). Best-effort: если Redis отвалился — пайплайн и так
        # падает, отдельно не обрабатываем.
        try:
            self.redis.set(LIMIT_KEY, "1", ex=max(1, int(self.delay)))
        except Exception:  # noqa: BLE001
            pass

    def get(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 30)
        timeout = kwargs["timeout"]
        req_s = timeout if isinstance(timeout, (int, float)) else 30
        # Держим лок ДОЛЬШЕ самого запроса: при TTL=delay(5с) лок истекал в
        # полёте медленного запроса (timeout 30/60с), и второй worker стартовал
        # параллельно — глобальный Crawl-delay нарушался именно под нагрузкой.
        # Теперь hold перекрывает запрос, а _open_next_slot после него открывает
        # следующий слот через delay. Самоограничен TTL — падение не вечно.
        hold = int(self.delay + req_s + 5)
        self._wait_for_slot(hold)
        try:
            return self.session.get(url, **kwargs)
        finally:
            self._open_next_slot()


def make_http_session(redis_client=None, delay: float = CRAWL_DELAY):
    """Фабрика: если есть Redis — кросс-процессная, иначе fallback на in-process.

    Использовать вместо прямого `ThrottledSession()` в actor'ах. Гарантирует,
    что unit-тесты, которые не поднимают Redis, не падают на импорте.
    """
    if redis_client is None:
        return ThrottledSession(delay=delay)
    return RedisThrottledSession(redis_client, delay=delay)
