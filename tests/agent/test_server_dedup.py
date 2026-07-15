"""P0-3b: агент не запускает второй прогон для того же submission_id.

Повторный POST /run (редоставка actor'а на Linux, ретрай после ложного
таймаута) не должен стартовать вторую гонку подачи — иначе двойная заявка.
"""

from __future__ import annotations

import threading
import time

import pytest

from agent import server as server_mod
from agent.protocol import RunRequest


def _mk_req(sub_id: int = 1) -> RunRequest:
    return RunRequest(
        submission_id=sub_id,
        anno_id=100,
        open_at_iso="2026-07-15T10:00:00+00:00",
        lot_bids=[],
        p12_b64="x",
        portal_password="p",
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    with server_mod._active_lock:
        server_mod._active.clear()
    yield
    with server_mod._active_lock:
        server_mod._active.clear()


def test_duplicate_run_does_not_start_second(monkeypatch):
    started: list[int] = []
    done = threading.Event()

    def fake_run(req):
        started.append(req.submission_id)
        done.set()

    monkeypatch.setattr(server_mod, "run", fake_run)
    req = _mk_req(1)

    assert server_mod._accept_run(req) is True
    assert server_mod._accept_run(req) is False  # дубль — не стартует

    assert done.wait(timeout=2.0)
    time.sleep(0.05)  # дать _run_and_track проставить terminal state
    assert started == [1]

    # Повтор после завершения — тоже дубль (не перезапускаем поданную заявку).
    assert server_mod._accept_run(req) is False


def test_distinct_submissions_run_independently(monkeypatch):
    started: list[int] = []
    lock = threading.Lock()
    barrier = threading.Event()

    def fake_run(req):
        with lock:
            started.append(req.submission_id)
        if len(started) >= 2:
            barrier.set()

    monkeypatch.setattr(server_mod, "run", fake_run)

    assert server_mod._accept_run(_mk_req(1)) is True
    assert server_mod._accept_run(_mk_req(2)) is True

    assert barrier.wait(timeout=2.0)
    assert sorted(started) == [1, 2]
