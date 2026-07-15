"""P0-1: fail-fast на небезопасном SECRET_KEY.

Дефолт публичен (репозиторий открыт) → подделка cookie-сессии = обход auth.
web и worker должны отказываться стартовать без реального ключа, кроме
явного dev-режима (GZ_NO_AUTH=1 / GZ_TEST_MODE=1).
"""

from __future__ import annotations

import pytest

from goszakup import config


def test_secret_key_is_safe_default(monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", config._INSECURE_SECRET_DEFAULT)
    assert config.secret_key_is_safe() is False


def test_secret_key_is_safe_real(monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", "b2c3-real-random-secret")
    assert config.secret_key_is_safe() is True


def test_require_raises_on_unsafe_in_prod(monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", config._INSECURE_SECRET_DEFAULT)
    monkeypatch.delenv("GZ_NO_AUTH", raising=False)
    monkeypatch.delenv("GZ_TEST_MODE", raising=False)
    with pytest.raises(RuntimeError, match="GZ_SECRET_KEY"):
        config.require_safe_secret_key("web")


def test_require_ok_in_dev_mode(monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", config._INSECURE_SECRET_DEFAULT)
    monkeypatch.delenv("GZ_TEST_MODE", raising=False)
    monkeypatch.setenv("GZ_NO_AUTH", "1")
    config.require_safe_secret_key("web")  # не должно бросить


def test_require_ok_with_real_key(monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", "real")
    monkeypatch.delenv("GZ_NO_AUTH", raising=False)
    monkeypatch.delenv("GZ_TEST_MODE", raising=False)
    config.require_safe_secret_key("web")  # не должно бросить


def test_web_startup_fails_without_secret(monkeypatch):
    # Рабочий каталог = прод, .env несёт реальный GZ_SECRET_KEY — форсируем
    # небезопасный дефолт, чтобы проверить именно fail-fast lifespan.
    monkeypatch.setattr(config, "SECRET_KEY", config._INSECURE_SECRET_DEFAULT)
    monkeypatch.delenv("GZ_NO_AUTH", raising=False)
    monkeypatch.delenv("GZ_TEST_MODE", raising=False)
    from starlette.testclient import TestClient

    from goszakup.web.app import app

    with pytest.raises(RuntimeError, match="GZ_SECRET_KEY"):
        with TestClient(app):
            pass
