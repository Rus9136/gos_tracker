"""P0/P1-5: агент не стреляет после close_at (deadline_guard в runner)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent import runner as runner_mod
from agent.protocol import RunRequest


class _FakeWizard:
    fired = False

    def __init__(self, req, on_status=None):
        pass

    def start(self):
        pass

    def warm_up(self):
        pass

    def submit_after_open(self):
        _FakeWizard.fired = True
        return {"status": "SUBMITTED", "app_id": 1}

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _patch_runner(monkeypatch):
    _FakeWizard.fired = False
    monkeypatch.setattr(runner_mod, "Wizard", _FakeWizard)
    monkeypatch.setattr(runner_mod, "wait_until", lambda *a, **k: datetime.now(UTC))
    monkeypatch.setattr(runner_mod, "report_result", lambda r: True)


def _req(*, close_iso):
    return RunRequest(
        submission_id=1,
        anno_id=100,
        open_at_iso=datetime.now(UTC).isoformat(),
        close_at_iso=close_iso,
        lot_bids=[],
        p12_b64="x",
        portal_password="p",
    )


def test_expired_deadline_skips_fire():
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    res = runner_mod.run(_req(close_iso=past))
    assert res.status == "SKIPPED"
    assert _FakeWizard.fired is False


def test_valid_deadline_fires():
    future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    res = runner_mod.run(_req(close_iso=future))
    assert res.status == "SUBMITTED"
    assert _FakeWizard.fired is True
