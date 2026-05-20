"""HTTP-клиент с rate-limit'ом (Crawl-delay 5s) на уровне сессии."""

from __future__ import annotations

import time
from threading import Lock

import requests

from ..config import CRAWL_DELAY, HTTP_HEADERS


class ThrottledSession:
    """requests.Session с гарантированной паузой между запросами."""

    def __init__(self, delay: float = CRAWL_DELAY) -> None:
        self.session = requests.Session()
        self.session.headers.update(HTTP_HEADERS)
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
