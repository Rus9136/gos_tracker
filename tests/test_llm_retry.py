"""Гейт 2: LLM ретраит транзиентные отказы, не только 429.

Раньше _call_llm ретраил лишь 429/queue_exceeded, а 5xx/таймаут/обрыв соединения
сразу проваливал лот — при массовом сетевом сбое пачка IT-лотов оставалась без
анализа до ручного вмешательства.
"""

from __future__ import annotations

import sys
import types

import pytest

from goszakup.classify import llm
from goszakup.db.models import Lot


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("429 queue_exceeded"),
        RuntimeError("503 Service Unavailable"),
        RuntimeError("500 internal server error"),
        RuntimeError("Bad Gateway"),
        TimeoutError("request timed out"),
        ConnectionError("connection reset by peer"),
        Exception("APITimeoutError: read timeout"),
        Exception("Model is temporarily unavailable, overloaded"),
    ],
)
def test_retryable_errors(exc):
    assert llm._is_retryable_llm_error(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("401 invalid api key"),
        RuntimeError("403 forbidden"),
        RuntimeError("400 bad request: schema mismatch"),
        ValueError("нечто детерминированное"),
    ],
)
def test_non_retryable_errors(exc):
    assert llm._is_retryable_llm_error(exc) is False


def _install_fake_cerebras(monkeypatch, create_fn):
    mod = types.ModuleType("cerebras.cloud.sdk")

    class _Cerebras:
        def __init__(self, api_key=None):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create_fn)
            )

    mod.Cerebras = _Cerebras
    monkeypatch.setitem(sys.modules, "cerebras.cloud.sdk", mod)
    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)


def _lot():
    return Lot(id=1, url="u", name="Разработка ИС", enstru="620000")


def test_call_llm_retries_transient_then_gives_up(monkeypatch):
    calls = {"n": 0}

    def create(**kw):
        calls["n"] += 1
        raise RuntimeError("503 Service Unavailable")

    _install_fake_cerebras(monkeypatch, create)
    out = llm._call_llm(_lot(), None, None)

    assert calls["n"] == 4  # 1 попытка + 3 ретрая
    assert out.result is None
    assert "транзиент" in (out.error or "")


def test_call_llm_no_retry_on_deterministic(monkeypatch):
    calls = {"n": 0}

    def create(**kw):
        calls["n"] += 1
        raise RuntimeError("401 invalid api key")

    _install_fake_cerebras(monkeypatch, create)
    out = llm._call_llm(_lot(), None, None)

    assert calls["n"] == 1  # без ретраев
    assert out.result is None
