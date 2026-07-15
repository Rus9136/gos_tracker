"""P0-3c: fire() не ретраит после отправки запроса.

ConnectError/ConnectTimeout — соединение не установлено, запрос не ушёл →
ретрай безопасен. ReadTimeout/обрыв ПОСЛЕ отправки → сервер мог принять
заявку; слепой ретрай задвоил бы подачу, поэтому одна попытка и UNKNOWN.
"""

from __future__ import annotations

import httpx
import pytest

from goszakup.autosubmit import fire as fire_mod


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, **kw):
        b = self.behaviors[self.calls]
        self.calls += 1
        if isinstance(b, Exception):
            raise b
        return b


@pytest.fixture
def patch_client(monkeypatch):
    holder = {}

    def _install(behaviors):
        fc = _FakeClient(behaviors)
        holder["client"] = fc
        monkeypatch.setattr(fire_mod.httpx, "Client", lambda **kw: fc)
        return fc

    return _install


def test_connect_error_is_retried(patch_client):
    fc = patch_client([httpx.ConnectError("no route"), httpx.ConnectError("no route")])
    r = fire_mod.fire(1, 2, cookies={}, retries=1)
    assert fc.calls == 2  # обе попытки использованы
    assert r.ok is False
    assert r.unknown is False


def test_connect_error_then_success(patch_client):
    fc = patch_client([httpx.ConnectTimeout("slow"), _Resp(200, payload={"status": "ok"})])
    r = fire_mod.fire(1, 2, cookies={}, retries=1)
    assert fc.calls == 2
    assert r.ok is True


def test_read_timeout_is_not_retried_unknown(patch_client):
    fc = patch_client([httpx.ReadTimeout("after send"), _Resp(200, payload={"status": "ok"})])
    r = fire_mod.fire(1, 2, cookies={}, retries=1)
    assert fc.calls == 1  # НЕ ретраим после отправки
    assert r.ok is False
    assert r.unknown is True
    assert r.error and r.error.startswith("UNKNOWN")


def test_success_first_try(patch_client):
    fc = patch_client([_Resp(200, payload={"id": 42})])
    r = fire_mod.fire(1, 2, cookies={}, retries=1)
    assert fc.calls == 1
    assert r.ok is True
    assert r.unknown is False
