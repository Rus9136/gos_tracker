"""Reaper зависших прогонов (jobs.ingest.close_stale_runs / find_active_run).

finished_at штатно закрывает Redis-pending-счётчик, но он эфемерный —
рестарт redis/воркера теряет счётчик и прогон висит «идущим» вечно. Reaper
закрывает такие по БД-heartbeat'у (last_progress_at).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from goszakup.db.models import ScrapeRun
from goszakup.jobs.ingest import _STALE_RUN_AFTER, close_stale_runs, find_active_run


def _ago(**kw) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


def _naive(dt: datetime | None) -> datetime | None:
    # SQLite в тестах возвращает naive datetime (на Postgres — timestamptz),
    # сравниваем по «стенным» компонентам без tzinfo.
    return dt.replace(tzinfo=None) if dt is not None else None


def test_stale_run_is_closed_and_not_active(db_session):
    # Прогон с давним heartbeat'ом и без finished_at — зависший.
    stale_hb = _ago(minutes=40)
    run = ScrapeRun(preset_id=None, started_at=_ago(hours=2), last_progress_at=stale_hb)
    db_session.add(run)
    db_session.commit()
    run_id = run.id

    closed = close_stale_runs(db_session)
    assert closed == 1

    db_session.expire_all()
    reaped = db_session.get(ScrapeRun, run_id)
    # finished_at = момент последней активности, не now().
    assert _naive(reaped.finished_at) == _naive(stale_hb)
    assert find_active_run(db_session) is None


def test_recent_run_stays_active(db_session):
    run = ScrapeRun(preset_id=None, started_at=_ago(hours=3), last_progress_at=_ago(minutes=1))
    db_session.add(run)
    db_session.commit()

    assert close_stale_runs(db_session) == 0
    active = find_active_run(db_session)
    assert active is not None and active.id == run.id
    assert active.finished_at is None


def test_legacy_run_without_heartbeat_falls_back_to_started_at(db_session):
    # У прогонов до фичи last_progress_at пустой — staleness по started_at.
    run = ScrapeRun(preset_id=None, started_at=_ago(hours=5))
    db_session.add(run)
    db_session.commit()
    # Перетираем дефолт _now(), эмулируя строку из старой схемы.
    run.last_progress_at = None
    db_session.commit()
    run_id = run.id

    assert close_stale_runs(db_session) == 1
    db_session.expire_all()
    assert db_session.get(ScrapeRun, run_id).finished_at is not None


def test_already_finished_run_untouched(db_session):
    finished = _ago(hours=4)
    run = ScrapeRun(
        preset_id=None,
        started_at=_ago(hours=5),
        last_progress_at=_ago(hours=4, minutes=30),
        finished_at=finished,
    )
    db_session.add(run)
    db_session.commit()

    assert close_stale_runs(db_session) == 0
    db_session.expire_all()
    assert _naive(db_session.get(ScrapeRun, run.id).finished_at) == _naive(finished)


def test_threshold_is_inactivity_based():
    # Документируем: порог про бездействие, а не про общий возраст прогона.
    assert _STALE_RUN_AFTER == timedelta(minutes=15)
