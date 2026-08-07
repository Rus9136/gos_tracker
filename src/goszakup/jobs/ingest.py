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
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.engine import SessionLocal, init_db
from ..db.models import ScrapeRun
from ..scraper.search import SearchParams
from ..scraper.statuses import STATUS_NAMES
from ..sources import make_source
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


def run_progress(run: ScrapeRun, r=None) -> dict:
    """Прогресс прогона для индикатора в UI.

    Прогон двухфазный, и знаменатель есть только у второй фазы: сперва обход
    выдачи (сколько всего объявлений — заранее неизвестно, goszakup отдаёт
    страницы до первой пустой), затем детали по собранному списку. Поэтому
    фаза `listing` честно неопределённая, а `details` считается от
    pending-счётчика: он говорит, сколько ОСТАЛОСЬ, знаменатель и точку
    отсчёта кладёт рядом `_set_pending`. По details_fetched знаменатель не
    восстановить — он растёт и от ретраев.

    Redis недоступен или ключи протухли — отдаём `unknown`: индикатор станет
    крутилкой без цифр. Ошибку наружу не пускаем, это украшение страницы."""
    if run.finished_at is not None:
        return {"finished": True, "phase": "done", "percent": 100}

    base = {"finished": False, "listing_count": run.listing_count or 0}
    try:
        left = r.get(f"goszakup:run:{run.id}:pending") if r is not None else None
        total = r.get(f"goszakup:run:{run.id}:total") if r is not None else None
        started = r.get(f"goszakup:run:{run.id}:details_started") if r is not None else None
    except Exception:  # redis лёг — не роняем страницу отчёта
        left = total = started = None

    if left is None or total is None:
        # Либо ещё идёт обход выдачи, либо Redis потерял счётчики.
        return {**base, "phase": "listing"}

    total, left = int(total), max(int(left), 0)
    done = max(total - left, 0)
    eta = None
    if started and done >= 3:
        # Средний темп по уже сделанным. Первые пара деталей не показательны
        # (прогрев соединения, кеш справочников) — отсюда порог.
        elapsed = max(time.time() - int(started), 0)
        eta = int(elapsed / done * left)
    return {
        **base,
        "phase": "details",
        "done": done,
        "total": total,
        "percent": int(done * 100 / total) if total else 0,
        "eta_seconds": eta,
    }


def active_run_of_kind(
    session: Session, note_prefix: str, *, before_id: int | None = None
) -> ScrapeRun | None:
    """Живой прогон того же вида (по префиксу note) — защита от дублей.

    Синки (bids/contracts/plans) идут десятками минут: bids-sync — это 500
    отдельных GraphQL-запросов, `TrdApp.buyId` не принимает массив. Если за
    это время в очередь попадёт второе такое же сообщение (лишний enqueue,
    редоставка после обрыва соединения с Redis, ручной запуск с CLI поверх
    крона), второй прогон возьмёт ТУ ЖЕ выборку: отметка `bids_synced_at`
    ставится в конце опроса объявления, поэтому дубль не «продвигает» работу,
    а удваивает нагрузку на OWS и на потоки воркера. Плюс в UI такие прогоны
    наслаиваются и «идёт прогон #N» не гаснет никогда.

    `before_id` — добор гонки: проверка «не идёт ли уже» и вставка своей строки
    не атомарны, и при залпе (после рестарта воркер разбирает накопленную
    очередь) два потока успевают пройти проверку до коммита друг друга —
    замерено, разница 10 мс. Поэтому после вставки прогон переспрашивает,
    нет ли живого прогона СТАРШЕ него, и если есть — уступает."""
    threshold = datetime.now(UTC) - _STALE_RUN_AFTER
    q = (
        select(ScrapeRun)
        .where(ScrapeRun.finished_at.is_(None))
        .where(ScrapeRun.note.startswith(note_prefix))
        .where(_progress_ts() >= threshold)
    )
    if before_id is not None:
        q = q.where(ScrapeRun.id < before_id)
    return session.scalar(q.order_by(ScrapeRun.started_at.desc()))


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
    source = make_source()
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
                    session, source, params,
                    categories=None,
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
