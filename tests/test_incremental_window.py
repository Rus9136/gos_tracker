"""Водяной знак инкрементального синка (jobs/incremental.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from goszakup.config import MIN_AMOUNT
from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Preset, ScrapeRun
from goszakup.jobs.incremental import (
    FIRST_RUN_SPAN,
    MAX_SPAN,
    NOTE_PREFIX_DAILY,
    WINDOW_MARGIN,
    daily_scan_params,
    sync_window,
)
from goszakup.scraper.statuses import ACTUAL_STATUSES

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def session():
    init_db()
    with SessionLocal() as s:
        s.query(ScrapeRun).delete()
        s.query(Preset).delete()
        s.commit()
        yield s


def _run(session, note, *, started, finished=True, errors=0):
    r = ScrapeRun(preset_id=None, note=note)
    session.add(r)
    session.flush()
    r.started_at = started
    r.finished_at = started + timedelta(minutes=5) if finished else None
    r.errors = errors
    session.commit()
    return r


def test_first_run_defaults_to_48h(session):
    dt_from, dt_to, clamped = sync_window(session, NOTE_PREFIX_DAILY, now=NOW)
    assert dt_to == NOW
    assert dt_from == NOW - FIRST_RUN_SPAN
    assert not clamped


def test_window_from_watermark_minus_margin(session):
    started = NOW - timedelta(hours=6)
    _run(session, f"{NOTE_PREFIX_DAILY}: x", started=started)
    dt_from, _, clamped = sync_window(session, NOTE_PREFIX_DAILY, now=NOW)
    assert dt_from == started - WINDOW_MARGIN
    assert not clamped


def test_window_clamped_after_long_downtime(session):
    _run(session, f"{NOTE_PREFIX_DAILY}: x", started=NOW - timedelta(days=30))
    dt_from, _, clamped = sync_window(session, NOTE_PREFIX_DAILY, now=NOW)
    assert dt_from == NOW - MAX_SPAN
    assert clamped


def test_failed_and_unfinished_and_foreign_runs_ignored(session):
    _run(session, f"{NOTE_PREFIX_DAILY}: err", started=NOW - timedelta(hours=2), errors=1)
    _run(session, f"{NOTE_PREFIX_DAILY}: open", started=NOW - timedelta(hours=3), finished=False)
    _run(session, "contracts-sync: чужой", started=NOW - timedelta(hours=1))
    good = _run(session, f"{NOTE_PREFIX_DAILY}: ok", started=NOW - timedelta(hours=10))
    dt_from, _, _ = sync_window(session, NOTE_PREFIX_DAILY, now=NOW)
    started = good.started_at if good.started_at.tzinfo else good.started_at.replace(tzinfo=UTC)
    assert dt_from == started - WINDOW_MARGIN


def test_daily_scan_params_union(session):
    session.add(Preset(name="а", kato="710000000", amount_from=500_000,
                       status_codes=[210, 220], active=True))
    session.add(Preset(name="б", kato="750000000", amount_from=300_000,
                       status_codes=[220, 240], active=True))
    session.add(Preset(name="выкл", kato="790000000", amount_from=1,
                       status_codes=[430], active=False))
    session.commit()
    statuses, amount = daily_scan_params(session)
    assert statuses == [210, 220, 240]  # объединение активных, без выключенных
    assert amount == 300_000


def test_daily_scan_params_fallback_when_no_presets(session):
    statuses, amount = daily_scan_params(session)
    assert statuses == list(ACTUAL_STATUSES)
    assert amount == MIN_AMOUNT
