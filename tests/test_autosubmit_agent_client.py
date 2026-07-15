"""P0-channel: канал Linux→agent требует токен и безопасный транспорт.

RunRequest несёт расшифрованные p12/пароль/PIN клиента — их нельзя слать без
авторизации канала и по незашифрованному http (кроме явного allowlist tailnet).
"""

from __future__ import annotations

import pytest

from goszakup.autosubmit.agent_client import AgentClient, AgentError
from goszakup.autosubmit.rpc import RunRequest


def _req() -> RunRequest:
    return RunRequest(
        submission_id=1,
        anno_id=1,
        open_at_iso="2026-07-15T10:00:00+00:00",
        lot_bids=[],
        p12_b64="x",
        portal_password="p",
    )


def test_dispatch_without_token_raises():
    c = AgentClient("https://agent", token=None)
    with pytest.raises(AgentError, match="TOKEN"):
        c.dispatch(_req())


def test_http_without_allowlist_raises():
    c = AgentClient("http://100.64.0.5:8799", token="t")
    with pytest.raises(AgentError, match="небезопасн"):
        c.dispatch(_req())


def test_https_transport_ok():
    AgentClient("https://agent", token="t")._validate_transport()  # не бросает


def test_http_in_allowlist_ok():
    AgentClient(
        "http://100.64.0.5:8799", token="t", allow_http_hosts=("100.64.0.5",)
    )._validate_transport()  # не бросает
