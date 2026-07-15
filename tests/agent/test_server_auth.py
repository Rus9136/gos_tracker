"""P0-channel: агент отказывается стартовать без токена (fail-closed).

Агент держит расшифрованные секреты клиента — принимать /run от кого угодно
без авторизации нельзя. Исключение — явный dev-режим GZ_AGENT_DEV=1.
"""

from __future__ import annotations

import pytest

from agent import config as agent_config
from agent import server as server_mod


def test_serve_refuses_without_token(monkeypatch):
    monkeypatch.setattr(agent_config, "AGENT_TOKEN", None)
    monkeypatch.setattr(agent_config, "AGENT_DEV", False)
    with pytest.raises(RuntimeError, match="GZ_AGENT_TOKEN"):
        server_mod._ensure_auth_configured()


def test_dev_mode_allows_no_token(monkeypatch):
    monkeypatch.setattr(agent_config, "AGENT_TOKEN", None)
    monkeypatch.setattr(agent_config, "AGENT_DEV", True)
    server_mod._ensure_auth_configured()  # не бросает


def test_token_configured_ok(monkeypatch):
    monkeypatch.setattr(agent_config, "AGENT_TOKEN", "secret")
    monkeypatch.setattr(agent_config, "AGENT_DEV", False)
    server_mod._ensure_auth_configured()  # не бросает
