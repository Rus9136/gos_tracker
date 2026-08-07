"""Защита синков от дублей и закрытие прогона при падении актора.

2026-08-07: в очереди goszakup_daily одновременно висели шесть сообщений
bids_sync_actor, каждое перемалывало те же 500 объявлений (выборка не
«продвигается»: bids_synced_at ставится в конце опроса). Прогоны наслаивались,
воркер голодал, а UI показывал «идёт прогон #N» без перерыва.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from goszakup.db.models import ScrapeRun
from goszakup.jobs.ingest import active_run_of_kind
from goszakup.queue.actors import _run_scope, _skip_duplicate


def _ago(**kw) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


def _run(session, note: str, **kw) -> ScrapeRun:
    run = ScrapeRun(preset_id=None, note=note, **kw)
    session.add(run)
    session.commit()
    return run


def test_live_run_of_same_kind_is_found(db_session):
    run = _run(db_session, "bids-sync: горизонт 45д, лимит 500",
               started_at=_ago(minutes=20), last_progress_at=_ago(minutes=1))
    found = active_run_of_kind(db_session, "bids-sync")
    assert found is not None and found.id == run.id
    assert _skip_duplicate(db_session, "bids-sync") is True


def test_other_kind_does_not_block(db_session):
    # Синки разных видов ходят в разные ручки API и друг другу не мешают.
    _run(db_session, "plans-sync: пункты плана свежее id=1",
         started_at=_ago(minutes=5), last_progress_at=_ago(minutes=1))
    assert active_run_of_kind(db_session, "bids-sync") is None
    assert _skip_duplicate(db_session, "bids-sync") is False


def test_finished_and_stale_runs_do_not_block(db_session):
    _run(db_session, "bids-sync: горизонт 45д, лимит 500",
         started_at=_ago(hours=2), last_progress_at=_ago(hours=1),
         finished_at=_ago(hours=1))
    # Зависший (нет прогресса дольше порога) тоже не должен запирать синк
    # навсегда — иначе один потерянный прогон отключил бы фичу до reaper'а.
    _run(db_session, "bids-sync: горизонт 45д, лимит 500",
         started_at=_ago(hours=3), last_progress_at=_ago(minutes=40))
    assert active_run_of_kind(db_session, "bids-sync") is None


def test_run_scope_closes_on_exception(db_session):
    # TimeLimitExceeded у dramatiq прилетает исключением в середину работы —
    # без finally прогон оставался с finished_at=NULL и висел в UI «идущим».
    with pytest.raises(RuntimeError):
        with _run_scope(db_session, "bids-sync: тест") as run_id:
            captured = run_id
            raise RuntimeError("time limit")

    db_session.expire_all()
    assert db_session.get(ScrapeRun, captured).finished_at is not None


def test_run_scope_closes_on_success(db_session):
    with _run_scope(db_session, "plans-sync: тест") as run_id:
        pass
    db_session.expire_all()
    assert db_session.get(ScrapeRun, run_id).finished_at is not None
