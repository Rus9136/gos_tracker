"""RedisThrottledSession + make_http_session — проверяем поведение mutex'а.

Используем fakeredis вместо реального Redis — это in-process реализация
Redis-протокола, идеально для unit-тестов.
"""

from __future__ import annotations

import time

import fakeredis
import pytest

from goszakup.queue.rate_limit import (
    LIMIT_KEY,
    RedisThrottledSession,
    make_http_session,
)
from goszakup.scraper.http import ThrottledSession


@pytest.fixture
def fake_redis():
    return fakeredis.FakeStrictRedis(decode_responses=True)


def test_first_call_passes_immediately(fake_redis):
    sess = RedisThrottledSession(fake_redis, delay=5.0)
    t0 = time.monotonic()
    sess._wait_for_slot()
    elapsed = time.monotonic() - t0
    # Первый _wait — должен пройти за <100ms (Redis-вызов локальный).
    assert elapsed < 0.1
    # Ключ должен быть установлен с TTL около 5с.
    assert fake_redis.exists(LIMIT_KEY)
    assert 4 <= fake_redis.ttl(LIMIT_KEY) <= 5


def test_second_call_blocks_until_ttl(fake_redis, monkeypatch):
    sess = RedisThrottledSession(fake_redis, delay=5.0)
    sess._wait_for_slot()  # Слот занят первым вызовом.

    # Подменяем sleep — иначе тест 5с висит. Сразу «прокручиваем время»:
    # удаляем ключ как будто TTL истёк.
    sleep_calls = []

    def fake_sleep(s):
        sleep_calls.append(s)
        if fake_redis.exists(LIMIT_KEY):
            fake_redis.delete(LIMIT_KEY)

    monkeypatch.setattr("time.sleep", fake_sleep)
    sess._wait_for_slot()
    # Должен был один раз попытаться, увидеть занятый слот, поспать, повторить.
    assert sleep_calls, "ожидали хотя бы один sleep при занятом слоте"
    # И снова занять.
    assert fake_redis.exists(LIMIT_KEY)


def test_lock_held_longer_than_request_then_released(fake_redis):
    # Лок должен перекрывать весь запрос (иначе истечёт в полёте медленного
    # запроса и второй worker стартует параллельно), а после — окно = delay.
    sess = RedisThrottledSession(fake_redis, delay=5.0)
    captured = {}

    class _FakeSession:
        headers: dict = {}

        def get(self, url, **kwargs):
            captured["ttl_during"] = fake_redis.ttl(LIMIT_KEY)
            return "resp"

    sess.session = _FakeSession()
    sess.get("http://x", timeout=30)

    assert captured["ttl_during"] > 5  # держится дольше delay (перекрывает timeout=30)
    assert 4 <= fake_redis.ttl(LIMIT_KEY) <= 5  # после запроса окно до след. слота = delay


def test_make_http_session_falls_back_without_redis():
    # redis_client=None — должен вернуть простой ThrottledSession.
    sess = make_http_session(None)
    assert isinstance(sess, ThrottledSession)


def test_make_http_session_uses_redis_when_provided(fake_redis):
    sess = make_http_session(fake_redis, delay=5.0)
    assert isinstance(sess, RedisThrottledSession)
    assert sess.redis is fake_redis
