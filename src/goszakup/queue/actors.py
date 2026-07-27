"""Dramatiq actors — три стадии пайплайна: listing → detail → llm.

Архитектура:

  daily_actor() ──> listing_actor(preset_id) ──> detail_actor(anno_id, run_id) ──> analyze_actor(lot_id, run_id)
       │                  │                              │
       │                  │                              ├─ Если есть IT-лоты — enqueue analyze_actor.
       │                  │                              └─ Decrement pending-counter; если 0 → закрыть run.
       │                  │
       │                  └─ Создаёт ScrapeRun, walks listing, ставит detail_actor для каждого нового/changed anno.
       │
       └─ Раз в день читает active presets, шлёт listing_actor для каждого.

Очереди именованные — позволяет иметь отдельных воркеров для CPU-bound (LLM)
и I/O-bound (listing/detail). Кросс-процессный rate-limit на goszakup —
через `RedisThrottledSession`; LLM в Cerebras рейтлимита оттуда не имеет.

Pending-counter (`goszakup:run:<id>:pending`) — атомарный INCR/DECR в Redis,
определяет момент «все detail-таски этого ScrapeRun завершены, можно
закрыть finished_at». Подсчёт идёт только по detail'ам — LLM-анализ
fire-and-forget относительно run-жизненного цикла, потому что аналитика
может уезжать на часы и блокировать закрытие run'а не должна.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import dramatiq
import redis
from sqlalchemy import select, update

from ..api.client import OwsApiError, OwsClient
from ..classify.it import classify
from ..classify.llm import analyze_and_save
from ..config import MIN_AMOUNT, OWS_TOKEN
from ..db.engine import SessionLocal
from ..db.models import Lot, Preset, ScrapeRun
from ..jobs.expire import expire_actual_lots
from ..jobs.ingest import close_stale_runs
from ..jobs.reconcile import find_orphan_stub_annos
from ..jobs.retention import cleanup_old_documents
from ..jobs.run_preset import (
    _apply_details,
    _apply_enstru_code,
    _save_announcement,
    _save_contracts,
    _save_documents,
    _upsert_lot_from_listing,
)
from ..scraper.search import SearchParams
from ..sources import ApiSource, make_source, mark_api_degraded

# Импорты ниже регистрируют actor'ы в брокере (воркер грузит именно этот модуль).
# Без них actor не зарегистрирован, и его задачи молча копятся в Redis независимо
# от --queues воркера — ровно то, что случилось с autosubmit (P0-2).
from .autosubmit import autosubmit_dispatch_actor  # noqa: E402,F401
from .broker import REDIS_URL, broker  # noqa: F401 — импорт broker до actor'ов обязателен
from .matching import enqueue_matches_for_lot  # noqa: E402 — fan-out хелпер для analyze_actor
from .notify import notify_actor  # noqa: E402,F401

log = logging.getLogger(__name__)


def _redis_client():
    # decode_responses=True — счётчики/значения возвращаются как str/int,
    # без bytes-возни. Один клиент на actor invocation — короткоживущий.
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


# === Pending-counter helpers ===

def _pending_key(run_id: int) -> str:
    return f"goszakup:run:{run_id}:pending"


def _set_pending(r, run_id: int, count: int) -> None:
    # TTL 24h на случай, если последний DECR не сработает (worker умер) —
    # ключ всё равно протухнет, не будем держать мусор вечно.
    r.set(_pending_key(run_id), count, ex=24 * 3600)


def _decrement_pending_and_maybe_close(r, run_id: int) -> None:
    key = _pending_key(run_id)
    remaining = r.decr(key)
    if remaining is None or remaining > 0:
        return
    # Все detail'ы прошли — закрываем run. Удаляем ключ, чтобы повторный
    # DECR от поздно прилетевшего ретрая не оставил отрицательное значение.
    with SessionLocal() as session:
        _close_run(session, run_id)
    r.delete(key)


def _touch_run(session, run_id: int) -> None:
    """Heartbeat: помечает, что по run'у только что был прогресс. На этом
    основан reaper зависших прогонов (jobs.ingest.close_stale_runs) — он
    не зависит от непёрсистентного Redis-счётчика."""
    session.execute(
        update(ScrapeRun)
        .where(ScrapeRun.id == run_id)
        .values(last_progress_at=datetime.now(UTC))
    )
    session.commit()


def _increment_run(session, run_id: int, **deltas) -> None:
    if not deltas:
        return
    cols = {k: ScrapeRun.__table__.c[k] + v for k, v in deltas.items()}
    cols["last_progress_at"] = datetime.now(UTC)  # инкремент счётчика = прогресс
    session.execute(update(ScrapeRun).where(ScrapeRun.id == run_id).values(**cols))
    session.commit()


def _update_run(session, run_id: int, **fields) -> None:
    if not fields:
        return
    fields["last_progress_at"] = datetime.now(UTC)
    session.execute(update(ScrapeRun).where(ScrapeRun.id == run_id).values(**fields))
    session.commit()


def _close_run(session, run_id: int) -> None:
    session.execute(
        update(ScrapeRun)
        .where(ScrapeRun.id == run_id)
        .values(finished_at=datetime.now(UTC))
    )
    session.commit()


# === Actors ===


@dramatiq.actor(queue_name="goszakup_daily", max_retries=0)
def daily_actor() -> None:
    """Ежедневный триггер: инкрементальный API-проход, либо обход preset'ов.

    С токеном OWS вместо 20 региональных сканов — один проход по
    lastUpdateDate (api_daily_actor) + синк договоров. Без токена —
    прежний fan-out listing_actor по активным preset'ам, бит-в-бит.
    """
    if OWS_TOKEN:
        log.info("daily_actor: инкрементальный API-путь")
        api_daily_actor.send()
        contracts_sync_actor.send()
        bids_sync_actor.send()
    else:
        with SessionLocal() as session:
            ids = [
                p.id
                for p in session.scalars(
                    select(Preset).where(Preset.active.is_(True)).order_by(Preset.id)
                )
            ]
        log.info("daily_actor: enqueue listing_actor x %d", len(ids))
        for pid in ids:
            listing_actor.send(pid)
    # Перед обходом гасим уже истёкшие — дёшево (локальный UPDATE по БД),
    # отдельно от ежечасного expire_actor, чтобы daily всегда оставлял
    # консистентную выдачу даже если таймер expire почему-то не сработал.
    expire_actor.send()


@dramatiq.actor(queue_name="goszakup_daily", max_retries=0, time_limit=30 * 60 * 1000)
def contracts_sync_actor(days_back: int | None = None) -> None:
    """Синк договоров/победителей из GraphQL Contract по окну lastUpdateDate.

    Обновляет только лоты, уже существующие в БД, — включая закрытые
    (закрывает пробел правила #5: закрытые не пересканируются, но договор
    к ним теперь приезжает отсюда). days_back — ручной override окна для
    бэкофилла с CLI. max_retries=0: следующий daily сам доберёт окно от
    старого водяного знака, ретрай дал бы дубль ScrapeRun.
    """
    if not OWS_TOKEN:
        return
    from ..jobs.contracts import sync_contracts
    from ..jobs.incremental import NOTE_PREFIX_CONTRACTS, sync_window

    r = _redis_client()
    with SessionLocal() as session:
        if days_back is not None:
            dt_to = datetime.now(UTC)
            dt_from = dt_to - timedelta(days=days_back)
            clamped = False
        else:
            dt_from, dt_to, clamped = sync_window(session, NOTE_PREFIX_CONTRACTS)
        if clamped:
            log.warning(
                "contracts-sync: окно обрезано потолком %s — часть изменений "
                "источника потеряна", dt_from,
            )
        run = ScrapeRun(
            preset_id=None,
            note=f"{NOTE_PREFIX_CONTRACTS}: {dt_from:%Y-%m-%d %H:%M}—{dt_to:%H:%M} UTC",
        )
        session.add(run)
        session.commit()
        run_id = run.id

        client = OwsClient(redis_client=r)
        try:
            stats = sync_contracts(
                session, client, dt_from=dt_from, dt_to=dt_to,
                on_progress=lambda s: _touch_run(session, run_id),
            )
        except OwsApiError as e:
            # Фолбэка в HTML здесь нет и не нужно: HTML-путь договоров живёт
            # в detail-фазе; окно доберёт следующий daily.
            log.warning("contracts-sync: OWS недоступен (%s) — окно доберём позже", e)
            _increment_run(session, run_id, errors=1)
            _close_run(session, run_id)
            return
        _update_run(
            session, run_id,
            listing_count=stats.scanned,
            new_lots=stats.created,
            updated_lots=stats.updated,
        )
        _close_run(session, run_id)
        log.info(
            "contracts-sync: scanned=%d matched=%d created=%d updated=%d winners=%d",
            stats.scanned, stats.matched, stats.created, stats.updated,
            stats.winners_filled,
        )


@dramatiq.actor(queue_name="goszakup_daily", max_retries=0, time_limit=60 * 60 * 1000)
def bids_sync_actor(limit: int = 500, horizon_days: int | None = None) -> None:
    """Синк заявок поставщиков с ценами (jobs/bids.py).

    Опрашивает объявления с прошедшим дедлайном, по которым победитель ещё
    не известен: заявки sealed-bid и до дедлайна их не видно, поэтому окно
    по дате подачи здесь не работает (подробнее — в модуле jobs/bids).
    Запрос — на объявление (TrdApp.buyId не берёт массив), отсюда time_limit
    вдвое больше, чем у остальных синков: 500 объявлений при 1 rps — минуты,
    но с ретраями API запас нужен.
    """
    if not OWS_TOKEN:
        return
    from ..jobs.bids import RETRY_HORIZON_DAYS, sync_bids

    r = _redis_client()
    horizon = horizon_days if horizon_days is not None else RETRY_HORIZON_DAYS
    with SessionLocal() as session:
        run = ScrapeRun(
            preset_id=None, note=f"bids-sync: горизонт {horizon}д, лимит {limit}"
        )
        session.add(run)
        session.commit()
        run_id = run.id

        client = OwsClient(redis_client=r)
        try:
            stats = sync_bids(
                session, client, horizon_days=horizon, limit=limit,
                on_progress=lambda s: _touch_run(session, run_id),
            )
        except OwsApiError as e:
            log.warning("bids-sync: OWS недоступен (%s) — доберём следующим daily", e)
            _increment_run(session, run_id, errors=1)
            _close_run(session, run_id)
            return
        _update_run(
            session, run_id,
            listing_count=stats.bids_seen,
            new_lots=stats.created,
            updated_lots=stats.updated,
            errors=stats.errors,
        )
        _close_run(session, run_id)
        log.info(
            "bids-sync: объявлений=%d заявок=%d created=%d updated=%d winners=%d errors=%d",
            stats.announcements, stats.bids_seen, stats.created, stats.updated,
            stats.winners_filled, stats.errors,
        )


@dramatiq.actor(queue_name="goszakup_daily", max_retries=0, time_limit=30 * 60 * 1000)
def api_daily_actor() -> None:
    """Один инкрементальный проход Lots по lastUpdateDate вместо 20 сканов.

    Статусы/минимальная сумма — объединение активных preset'ов (они остаются
    конфигурацией покрытия); регион лота — из точки поставки (клиентского
    kato-фильтра нет, проход общереспубликанский). Дальше — тот же конвейер,
    что у listing_actor: IT-фильтр, upsert, detail_actor c pending-счётчиком.
    max_retries=0: при ошибке фолбэк на preset-обход внутри, ретрай дал бы
    двойной fan-out.
    """
    from ..jobs.incremental import NOTE_PREFIX_DAILY, daily_scan_params, sync_window

    r = _redis_client()
    with SessionLocal() as session:
        dt_from, dt_to, clamped = sync_window(session, NOTE_PREFIX_DAILY)
        if clamped:
            log.warning(
                "api-daily: окно обрезано потолком %s (простой >7 дней) — "
                "рекомендуется ручной прогон preset'ов", dt_from,
            )
        status_codes, amount_from = daily_scan_params(session)

        run = ScrapeRun(
            preset_id=None,
            note=f"{NOTE_PREFIX_DAILY}: {dt_from:%Y-%m-%d %H:%M}—{dt_to:%H:%M} UTC",
        )
        session.add(run)
        session.commit()
        run_id = run.id

        source = ApiSource(redis_client=r)
        new_hits: list = []
        status_changes: list = []
        listing_count = 0
        try:
            for hit, region in source.iter_updated_lots(
                dt_from=dt_from, dt_to=dt_to,
                status_codes=status_codes, amount_from=amount_from,
            ):
                listing_count += 1
                # Проект только про IT: не-IT лоты не храним и не парсим вообще.
                cat = classify(hit.enstru, hit.lot_name)
                if cat is None:
                    continue
                _upsert_lot_from_listing(
                    session, hit, kato=region,
                    on_new=new_hits, on_status_change=status_changes,
                )
                if listing_count % 100 == 0:
                    session.commit()
                    _touch_run(session, run_id)
            session.commit()
        except OwsApiError as e:
            session.rollback()
            log.warning(
                "api-daily: OWS недоступен (%s) — фолбэк на preset-обход", e
            )
            mark_api_degraded(r, "api-daily", e)
            _increment_run(session, run_id, errors=1)
            _close_run(session, run_id)
            preset_ids = session.scalars(
                select(Preset.id).where(Preset.active.is_(True)).order_by(Preset.id)
            ).all()
            for pid in preset_ids:
                listing_actor.send(pid)
            return
        except Exception:
            log.exception("api-daily failed")
            session.rollback()
            _increment_run(session, run_id, errors=1)
            _close_run(session, run_id)
            raise

        targets = {h.announcement_id for h in new_hits}
        for h, _prev in status_changes:
            targets.add(h.announcement_id)

        _update_run(
            session, run_id,
            listing_count=listing_count,
            new_lots=len(new_hits),
            updated_lots=len(status_changes),
        )
        if not targets:
            _close_run(session, run_id)
            return
        _set_pending(r, run_id, len(targets))
        for anno_id in targets:
            detail_actor.send(anno_id, run_id)


@dramatiq.actor(queue_name="goszakup_daily", max_retries=0)
def expire_actor() -> None:
    """Снять с «актуальных» лоты, у которых истёк срок приёма заявок.

    Гоняется ежечасно systemd-таймером (goszakup-expire.timer). К goszakup не
    ходит — только UPDATE по БД, поэтому быстрый и без rate-limit."""
    with SessionLocal() as session:
        n = expire_actual_lots(session)
    log.info("expire_actor: снято %d лотов", n)
    # Заодно фоном гасим зависшие прогоны (finished_at завязан на эфемерный
    # Redis-счётчик — рестарт redis/воркера теряет его). Делаем в той же
    # ежечасной задаче, чтобы UI не показывал «идёт прогон #N» бесконечно.
    with SessionLocal() as session:
        closed = close_stale_runs(session)
    if closed:
        log.info("expire_actor: закрыто %d зависших прогонов", closed)
    # Retention скачанных ТЗ — чтобы data/docs/ не рос вечно.
    with SessionLocal() as session:
        cleanup_old_documents(session)
    # И дозаполняем объявления-заглушки, осиротевшие при обрыве листинга.
    reconcile_actor.send()


@dramatiq.actor(queue_name="goszakup_daily", max_retries=0)
def reconcile_actor() -> None:
    """Дозаполнить объявления-заглушки без деталей (оборванный листинг /
    потерянная detail-задача) — ставит detail_actor для затронутых объявлений.

    Self-healing: детали к таким лотам иначе не приедут никогда (на ретрае
    листинга они уже «не новые»). Ставится ежечасно из expire_actor."""
    r = _redis_client()
    with SessionLocal() as session:
        anno_ids = find_orphan_stub_annos(session)
        if not anno_ids:
            return
        run = ScrapeRun(preset_id=None, note="reconcile: дозаполнение заглушек")
        session.add(run)
        session.commit()
        run_id = run.id
    _set_pending(r, run_id, len(anno_ids))
    for anno_id in anno_ids:
        detail_actor.send(anno_id, run_id)
    log.info("reconcile: %d объявлений на дозаполнение (run %d)", len(anno_ids), run_id)


@dramatiq.actor(
    queue_name="goszakup_listing",
    max_retries=2,
    time_limit=30 * 60 * 1000,  # listing на 20-страничной выдаче редко >10 мин
)
def listing_actor(preset_id: int) -> None:
    """Walk listing одного preset'а, enqueue detail-актёры для новых/changed."""
    r = _redis_client()
    source = make_source(r)

    with SessionLocal() as session:
        preset = session.get(Preset, preset_id)
        if preset is None:
            log.warning("listing_actor: preset %d не найден", preset_id)
            return

        run = ScrapeRun(preset_id=preset_id)
        session.add(run)
        session.commit()
        run_id = run.id

        params = SearchParams(
            kato=preset.kato or "",
            amount_from=preset.amount_from or MIN_AMOUNT,
            amount_to=preset.amount_to,
            status_codes=list(preset.status_codes or []),
        )
        it_categories = list(preset.it_categories or [])

        new_hits = []
        status_changes = []
        listing_count = 0

        try:
            for hit in source.iter_listing(params):
                listing_count += 1
                # Проект только про IT: не-IT лоты не храним и не парсим вообще.
                cat = classify(hit.enstru, hit.lot_name)
                if cat is None:
                    continue
                if it_categories and cat not in it_categories:
                    continue
                _upsert_lot_from_listing(
                    session, hit, kato=params.kato,
                    on_new=new_hits, on_status_change=status_changes,
                )
                if listing_count % 100 == 0:
                    session.commit()
                    _touch_run(session, run_id)  # heartbeat на долгом проходе
            session.commit()
        except Exception:
            log.exception("listing_actor failed for preset %d", preset_id)
            session.rollback()
            _increment_run(session, run_id, errors=1)
            _close_run(session, run_id)
            raise  # отдаём Dramatiq для ретрая (max_retries=2)

        # Сводим anno_id, по которым нужно делать details (новые + смена статуса).
        targets = {h.announcement_id for h in new_hits}
        for h, _prev in status_changes:
            targets.add(h.announcement_id)

        _update_run(
            session, run_id,
            listing_count=listing_count,
            new_lots=len(new_hits),
            updated_lots=len(status_changes),
        )

        if not targets:
            _close_run(session, run_id)
            return

        # Pending-counter: detail_actor'ы DECR'ят его на завершении, при 0 закроют run.
        _set_pending(r, run_id, len(targets))
        for anno_id in targets:
            detail_actor.send(anno_id, run_id)


@dramatiq.actor(
    queue_name="goszakup_detail",
    max_retries=3,
    min_backoff=10_000,    # 10s
    max_backoff=10 * 60_000,  # 10 min
    time_limit=10 * 60 * 1000,
)
def detail_actor(
    anno_id: int,
    run_id: int,
    with_docs: bool = True,
    with_llm: bool = True,
    only_it_lots: bool = True,
) -> None:
    """Fetch деталей одного объявления, save в БД, enqueue LLM для IT-лотов.

    Флаги `with_docs` / `with_llm` управляют тяжёлыми побочками — нужны
    для ad-hoc сканирования (`scan_actor`), где обычно хочется получить
    organizer/контакты, но не качать пачки PDF и не дёргать Cerebras.

    `only_it_lots=True` (default для daily-прогонов): пропускаем announcement'ы,
    у которых в БД нет ни одного лота с it_category. Это даёт ~10× ускорения
    фазы 2 для общереспубликанских прогонов, где IT ≈ 9% от всех лотов.
    `scan_actor`/`ingest_actor` передают False — там фильтрация не нужна.
    """
    r = _redis_client()

    # Heartbeat в начале каждой попытки: даже если details уезжают в ретрай
    # с backoff'ом (до 10 мин), reaper не сочтёт run зависшим — он живой.
    with SessionLocal() as session:
        _touch_run(session, run_id)

    if only_it_lots:
        with SessionLocal() as session:
            has_it = session.execute(
                select(Lot.id)
                .where(Lot.announcement_id == anno_id, Lot.it_category.is_not(None))
                .limit(1)
            ).first() is not None
        if not has_it:
            _decrement_pending_and_maybe_close(r, run_id)
            return

    source = make_source(r)

    it_lot_ids: list[int] = []
    try:
        with SessionLocal() as session:
            detail = source.fetch_announcement(anno_id)
            anno = _save_announcement(session, detail)
            lots = session.scalars(
                select(Lot).where(Lot.announcement_id == anno.id)
            ).all()
            for lot in lots:
                _apply_details(session, lot, detail)
                _apply_enstru_code(session, lot, source)
            lots_by_number = {lt.number: lt for lt in lots if lt.number}
            _save_contracts(session, lots_by_number, detail)
            new_docs = 0
            if with_docs:
                new_docs = _save_documents(session, anno, detail, source)
            session.commit()
            _increment_run(
                session, run_id, details_fetched=1, new_documents=new_docs
            )
            if with_llm:
                it_lot_ids = [lt.id for lt in lots if lt.it_category]
    except Exception:
        log.exception("detail_actor failed for anno=%s run=%s", anno_id, run_id)
        with SessionLocal() as session:
            _increment_run(session, run_id, errors=1)
        # Pending DECR делаем ТОЛЬКО на терминальной неудаче (последний ретрай).
        # `CurrentMessage` middleware даёт доступ к retry-state; если retries
        # ещё остались — raise, Dramatiq поставит сообщение в delay-очередь.
        msg = dramatiq.middleware.CurrentMessage.get_current_message()
        retries = (msg.options or {}).get("retries", 0) if msg else 0
        actor_max = detail_actor.options.get("max_retries", 0)
        if retries < actor_max:
            raise
        # Иначе считаем «дальше не пытаемся» и двигаем pending — иначе run
        # никогда не закроется.
        _decrement_pending_and_maybe_close(r, run_id)
        return

    for lot_id in it_lot_ids:
        analyze_actor.send(lot_id, run_id)
    _decrement_pending_and_maybe_close(r, run_id)


@dramatiq.actor(
    queue_name="goszakup_llm",
    max_retries=2,
    min_backoff=10_000,
    max_backoff=60_000,
    time_limit=5 * 60 * 1000,
)
def analyze_actor(lot_id: int, run_id: int) -> None:
    """LLM-анализ одного лота. Не влияет на закрытие run'а (fire-and-forget)."""
    with SessionLocal() as session:
        lot = session.get(Lot, lot_id)
        if lot is None:
            log.warning("analyze_actor: lot %d не найден", lot_id)
            return
        ok = analyze_and_save(session, lot)
        if ok:
            session.commit()
            _increment_run(session, run_id, llm_analyzed=1)
            # Fan-out: свежеразобранный лот матчим против активных запросов
            # пользователей (pre-filter по scope внутри). Лоты без нового
            # анализа сюда не идут — их подберёт backfill при создании запроса.
            enqueue_matches_for_lot(session, lot)


# === Ad-hoc ingest по БИН — аналогичный 3-стейдж pipeline ===


@dramatiq.actor(
    queue_name="goszakup_listing",
    max_retries=2,
    time_limit=60 * 60 * 1000,  # ingest по БИН за N лет может идти час
)
def ingest_actor(
    run_id: int,
    customer_bin: str,
    year_from: int,
    year_to: int,
    trade_type: str = "",
    status_codes: list[int] | None = None,
    amount_from: int = 0,
    amount_to: int | None = None,
) -> None:
    """Аналог listing_actor, но по БИН за период (без LLM, без документов).

    ScrapeRun уже создан в create_ingest_run — этот actor только исполняет.
    Документы и LLM подтягиваются вручную с UI на странице лота.
    """

    from ..scraper.search import SearchParams

    r = _redis_client()
    source = make_source(r)
    status_codes = list(status_codes or [])
    targets: set[int] = set()

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
                year_targets = _walk_listing(session, source, params, run_id)
                targets |= year_targets
                log.info(
                    "ingest run #%d year=%d: %d new targets",
                    run_id, year, len(year_targets),
                )
        except Exception:
            log.exception("ingest_actor failed for run %d", run_id)
            _increment_run(session, run_id, errors=1)
            _close_run(session, run_id)
            raise

    # Для ad-hoc ingest документы и LLM НЕ дёргаем (как было в jobs/ingest.py).
    # Просто закрываем run.
    if not targets:
        with SessionLocal() as s:
            _close_run(s, run_id)
        return

    _set_pending(r, run_id, len(targets))
    for anno_id in targets:
        # ingest по БИН — пользователь явно хочет все лоты этого заказчика,
        # включая не-IT. only_it_lots=False обходит IT-skip-guard в detail_actor.
        detail_actor.send(anno_id, run_id, only_it_lots=False)


# === Ad-hoc сканирование (UI: /scan) — kato/amount/status/IT-категории ===


@dramatiq.actor(
    queue_name="goszakup_listing",
    max_retries=2,
    time_limit=2 * 60 * 60 * 1000,  # «весь РК» при шаге 5с может идти часа полтора
)
def scan_actor(
    run_id: int,
    kato: str,
    amount_from: int,
    amount_to: int | None,
    status_codes: list[int],
    it_categories: list[str],
    listing_only: bool = False,
    with_docs: bool = True,
    with_llm: bool = True,
) -> None:
    """Ad-hoc прогон по произвольным SearchParams + опциональному IT-prefilter.

    Семантически близко к `listing_actor` (тот же two-phase pipeline), но
    параметры приходят из UI-формы, а не из Preset. ScrapeRun уже создан в
    `jobs/scan.create_scan_run` — этот actor только исполняет.
    """

    from ..scraper.search import SearchParams

    r = _redis_client()
    source = make_source(r)
    status_codes = list(status_codes or [])
    it_categories = list(it_categories or [])

    params = SearchParams(
        kato=kato or "",
        amount_from=amount_from,
        amount_to=amount_to,
        status_codes=status_codes,
    )

    new_hits = []
    status_changes = []
    listing_count = 0

    with SessionLocal() as session:
        try:
            for hit in source.iter_listing(params):
                listing_count += 1
                if it_categories:
                    cat = classify(hit.enstru, hit.lot_name)
                    if cat not in it_categories:
                        continue
                _upsert_lot_from_listing(
                    session, hit, kato=params.kato,
                    on_new=new_hits, on_status_change=status_changes,
                )
                if listing_count % 100 == 0:
                    session.commit()
                    _touch_run(session, run_id)  # heartbeat на долгом проходе
            session.commit()
        except Exception:
            log.exception("scan_actor listing failed for run %d", run_id)
            session.rollback()
            _increment_run(session, run_id, errors=1)
            _close_run(session, run_id)
            raise  # Dramatiq ретрайнет

        _update_run(
            session, run_id,
            listing_count=listing_count,
            new_lots=len(new_hits),
            updated_lots=len(status_changes),
        )

        if listing_only:
            _close_run(session, run_id)
            return

        targets = {h.announcement_id for h in new_hits}
        for h, _prev in status_changes:
            targets.add(h.announcement_id)

        if not targets:
            _close_run(session, run_id)
            return

    _set_pending(r, run_id, len(targets))
    for anno_id in targets:
        # scan по UI: пользователь сам выбрал IT-категории в форме — если
        # хочет non-IT, он указал it_categories=[] и листинг уже это
        # пропустил. На уровне detail не фильтруем — не сужаем ad-hoc-запрос.
        detail_actor.send(anno_id, run_id, with_docs, with_llm, only_it_lots=False)


def _walk_listing(
    session, source, params, run_id: int,
) -> set[int]:
    """Helper для ingest_actor: один проход по году. Возвращает уникальные anno_id."""
    new_hits: list = []
    status_changes: list = []
    targets: set[int] = set()
    listing_count = 0
    try:
        for hit in source.iter_listing(params):
            listing_count += 1
            _upsert_lot_from_listing(
                session, hit, kato=params.kato or "",
                on_new=new_hits, on_status_change=status_changes,
            )
            if listing_count % 100 == 0:
                session.commit()
                _touch_run(session, run_id)  # heartbeat на долгом проходе
        session.commit()
    except Exception:
        session.rollback()
        raise

    for h in new_hits:
        targets.add(h.announcement_id)
    for h, _prev in status_changes:
        targets.add(h.announcement_id)

    _increment_run(
        session, run_id,
        listing_count=listing_count,
        new_lots=len(new_hits),
        updated_lots=len(status_changes),
    )
    return targets
