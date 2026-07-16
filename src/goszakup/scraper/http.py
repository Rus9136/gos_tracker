"""HTTP-клиент с rate-limit'ом (Crawl-delay 5s) на уровне сессии."""

from __future__ import annotations

import time
from threading import Lock

import requests

from ..config import CRAWL_DELAY, GZ_PROXY_URL, HTTP_HEADERS


def build_goszakup_session() -> requests.Session:
    """requests.Session с заголовками и (при GZ_PROXY_URL) KZ-туннелем.

    Прямые коннекты к goszakup.gov.kz с зарубежного IP периодически массово
    дропаются на уровне TCP (2026-07-16: ConnectTimeout на листингах и
    деталях, через туннель — ответ <1с; v3bl так геоблочен давно). Сессия
    ходит только на goszakup-хосты, поэтому прокси вешаем на неё целиком.
    """
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    if GZ_PROXY_URL:
        s.proxies = {"http": GZ_PROXY_URL, "https": GZ_PROXY_URL}
    return s


class ThrottledSession:
    """requests.Session с гарантированной паузой между запросами."""

    def __init__(self, delay: float = CRAWL_DELAY) -> None:
        self.session = build_goszakup_session()
        self.delay = delay
        self._last_request_at = 0.0
        self._lock = Lock()

    def _wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_request_at = time.monotonic()

    def get(self, url: str, **kwargs) -> requests.Response:
        self._wait()
        kwargs.setdefault("timeout", 30)
        return self.session.get(url, **kwargs)
