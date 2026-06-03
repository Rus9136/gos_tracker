"""Ad-hoc загрузка лотов по БИН за период.

Отличия от обычного preset-прогона:
* без скачивания документов (download_docs=False) и без LLM (run_llm=False);
* один ScrapeRun агрегирует итерацию по нескольким годам;
* preset_id остаётся NULL, человекочитаемая подпись пишется в ScrapeRun.note.

Документы догружаются отдельно — кнопкой на карточке лота
(POST /lot/{id}/fetch_documents).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.engine import SessionLocal, init_db
from ..db.models import ScrapeRun
from ..scraper.http import ThrottledSession
from ..scraper.search import SearchParams
from ..scraper.statuses import STATUS_NAMES
from .run_preset import RunStats, execute_search

log = logging.getLogger(__name__)

# Если по незавершённому прогону НЕТ прогресса (heartbeat last_progress_at)
# дольше этого срока — считаем зависшим. Живой прогон бьёт heartbeat каждые
# несколько секунд (см. queue/actors._touch_run), так что 15 минут тишины
# надёжно означают «воркер/redis умер, finished_at уже никто не проставит».
# Порог именно по бездействию, а не по возрасту старта: «весь РК»-скан идёт
# часами и при этом жив.
_STALE_RUN_AFTER = timedelta(minutes=15)


def _format_trade_type(trade_type: str) -> str:
    return {"g": "товары", "s": "услуги", "r": "работы"}.get(trade_type, "")


def _build_note(
    customer_bin: str,
    year_from: int,
    year_to: int,
    trade_type: str,
    status_codes: list[int],
    amount_from: int,
    amount_to: int | None,
) -> str:
    parts = [f"БИН {customer_bin}"]
    if year_from == year_to:
        parts.append(str(year_from))
    else:
        parts.append(f"{year_from}–{year_to}")
    tt = _format_trade_type(trade_type)
    if tt:
        parts.append(tt)
    if status_codes:
        names = [STATUS_NAMES.get(c, str(c)) for c in status_codes]
        parts.append("статусы: " + ", ".join(names))
    if amount_from:
        parts.append(f"от {amount_from:,} ₸".replace(",", " "))
    if amount_to:
        parts.append(f"до {amount_to:,} ₸".replace(",", " "))
    return " · ".join(parts)


def _progress_ts():
    # last_progress_at у прогонов до этой фичи пустой — фоллбэк на started_at.
    return func.coalesce(ScrapeRun.last_progress_at, ScrapeRun.started_at)


def close_stale_runs(session: Session) -> int:
    """Проставляет finished_at прогонам, по которым давно нет прогресса.

    Штатно прогон закрывает Redis-pending-счётчик (queue/actors), но
    goszakup-redis непёрсистентный (--save "" --appendonly no): рестарт
    контейнера теряет и счётчик, и очередь detail-тасок — декрементить
    больше некому, finished_at навсегда остаётся NULL, а UI бесконечно
    показывает «идёт прогон #N». Подстраховываемся по БД-heartbeat'у:
    finished_at = момент последней активности (last_progress_at)."""
    threshold = datetime.now(UTC) - _STALE_RUN_AFTER
    stale = session.scalars(
        select(ScrapeRun)
        .where(ScrapeRun.finished_at.is_(None))
        .where(_progress_ts() < threshold)
    ).all()
    for run in stale:
        run.finished_at = run.last_progress_at or run.started_at
    if stale:
        session.commit()
    return len(stale)


def find_active_run(session: Session) -> ScrapeRun | None:
    """Возвращает реально идущий прогон (с недавним прогрессом), если есть.

    Сначала закрывает зависшие — чтобы и здесь, и на /runs, и на /scan
    показывались только живые прогоны."""
    close_stale_runs(session)
    threshold = datetime.now(UTC) - _STALE_RUN_AFTER
    return session.scalar(
        select(ScrapeRun)
        .where(ScrapeRun.finished_at.is_(None))
        .where(_progress_ts() >= threshold)
        .order_by(ScrapeRun.started_at.desc())
    )


def create_ingest_run(
    *,
    customer_bin: str,
    year_from: int,
    year_to: int,
    trade_type: str = "",
    status_codes: list[int] | None = None,
    amount_from: int = 0,
    amount_to: int | None = None,
) -> int:
    """Создаёт ScrapeRun запись и возвращает её id. Не запускает скрейп —
    это делает execute_ingest_run, обычно в BackgroundTasks."""
    init_db()
    if not customer_bin.strip():
        raise ValueError("customer_bin обязателен")
    if year_from > year_to:
        raise ValueError("year_from > year_to")

    with SessionLocal() as session:
        existing = find_active_run(session)
        if existing is not None:
            raise RuntimeError(
                f"уже идёт прогон #{existing.id} (старт {existing.started_at})"
            )
        note = _build_note(
            customer_bin, year_from, year_to, trade_type,
            list(status_codes or []), amount_from, amount_to,
        )
        run = ScrapeRun(preset_id=None, note=note)
        session.add(run)
        session.commit()
        return run.id


def execute_ingest_run(
    run_id: int,
    *,
    customer_bin: str,
    year_from: int,
    year_to: int,
    trade_type: str = "",
    status_codes: list[int] | None = None,
    amount_from: int = 0,
    amount_to: int | None = None,
) -> None:
    """Исполняет уже созданный run: цикл по годам, без документов и LLM.

    Любая ошибка ловится — run всё равно финализируется, чтобы UI не висел
    в состоянии «в работе».
    """
    init_db()
    http = ThrottledSession()
    total = RunStats()
    status_codes = list(status_codes or [])

    log.info(
        "ingest run #%d start: bin=%s years=%d-%d trade=%r statuses=%s",
        run_id, customer_bin, year_from, year_to, trade_type, status_codes,
    )

    with SessionLocal() as session:
        try:
            for year in range(year_from, year_to + 1):
                params = SearchParams(
                    customer_bin=customer_bin,
                    trade_type=trade_type,
                    status_codes=status_codes,
                    amount_from=amount_from,
                    amount_to=amount_to,
                    year=year,
                )
                stats = execute_search(
                    session, http, params,
                    it_categories=None,
                    download_docs=False,
                    run_llm=False,
                )
                total.add(stats)
                log.info(
                    "ingest run #%d year=%d: listing=%d new=%d updated=%d errors=%d",
                    run_id, year, stats.listing_count, stats.new_lots,
                    stats.updated_lots, stats.errors,
                )
        except Exception as e:  # pragma: no cover — finalize в любом случае
            total.errors += 1
            log.exception("ingest run #%d failed: %s", run_id, e)
        finally:
            run = session.get(ScrapeRun, run_id)
            if run is not None:
                run.finished_at = datetime.now(UTC)
                run.listing_count = total.listing_count
                run.details_fetched = total.details_fetched
                run.new_lots = total.new_lots
                run.updated_lots = total.updated_lots
                run.new_documents = total.new_documents
                run.llm_analyzed = total.llm_analyzed
                run.errors = total.errors
                session.commit()
            log.info(
                "ingest run #%d done: listing=%d new=%d updated=%d errors=%d",
                run_id, total.listing_count, total.new_lots,
                total.updated_lots, total.errors,
            )
