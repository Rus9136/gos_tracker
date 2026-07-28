"""Один прогон preset: listing → diff с БД → детали для новых/изменённых → upsert."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..classify.it import classify
from ..classify.llm import analyze_and_save
from ..db.engine import SessionLocal, init_db
from ..db.models import (
    Announcement,
    Contract,
    Document,
    Lot,
    LotStatusHistory,
    Organization,
    Preset,
    ScrapeRun,
)
from ..scraper.announce import AnnouncementDetail
from ..scraper.modal_files import is_tz_like_name
from ..scraper.search import ListingHit, SearchParams
from ..scraper.statuses import is_actual
from ..sources import DataSource, make_source

log = logging.getLogger(__name__)


def _find_org(session: Session, bin_: str | None, name: str) -> Organization | None:
    org = None
    if bin_:
        org = session.scalar(select(Organization).where(Organization.bin == bin_))
    if org is None and name:
        org = session.scalar(select(Organization).where(Organization.name == name))
    return org


def _get_or_create_org(
    session: Session, *, bin_: str | None, name: str
) -> Organization | None:
    name = (name or "").strip()
    bin_ = (bin_ or "").strip() or None
    if not name and not bin_:
        return None
    org = _find_org(session, bin_, name)
    if org is None:
        # Гонку (параллельный прогон создал ту же организацию) ловим savepoint'ом:
        # уникальность по bin / частичный uq_org_name_no_bin даёт IntegrityError,
        # мы откатываем только вставку и берём созданную конкурентом запись —
        # иначе на /organizations копились бы дубли заказчиков (P1, Гейт 2).
        try:
            with session.begin_nested():
                org = Organization(bin=bin_, name=name or bin_ or "")
                session.add(org)
                session.flush()
        except IntegrityError:
            org = _find_org(session, bin_, name)
            if org is None:
                raise
    if bin_ and not org.bin:
        org.bin = bin_
    if name and not org.name:
        org.name = name
    return org


def _save_announcement(
    session: Session, detail: AnnouncementDetail
) -> Announcement:
    anno = session.get(Announcement, detail.id)
    organizer = _get_or_create_org(
        session, bin_=detail.organizer_bin, name=detail.organizer_name
    )
    if anno is None:
        anno = Announcement(id=detail.id, url=detail.url)
        session.add(anno)
    anno.number = detail.number or anno.number
    anno.url = detail.url
    anno.method = detail.method or anno.method
    anno.purchase_type = detail.purchase_type or anno.purchase_type
    anno.subject_type = detail.subject_type or anno.subject_type
    anno.organizer_id = organizer.id if organizer else anno.organizer_id
    anno.organizer_address = detail.organizer_address or anno.organizer_address
    anno.lots_count = detail.lots_count if detail.lots_count is not None else anno.lots_count
    anno.total_amount = detail.total_amount if detail.total_amount is not None else anno.total_amount
    anno.attributes = detail.attributes or anno.attributes
    if detail.publish_date:
        anno.publish_date = detail.publish_date
    if detail.application_start:
        anno.application_start = detail.application_start
    if detail.application_end:
        anno.application_end = detail.application_end
    anno.contact_name = detail.contact_name or anno.contact_name
    anno.contact_role = detail.contact_role or anno.contact_role
    anno.contact_email = detail.contact_email or anno.contact_email
    session.flush()
    return anno


def _ensure_announcement_stub(session: Session, anno_id: int, url: str) -> None:
    """На Postgres FK строгий: вставить Lot с announcement_id, для которого
    нет записи Announcement — нельзя. На SQLite это раньше проходило, потому
    что FK там не enforced по умолчанию. Создаём stub с минимумом полей;
    `_save_announcement` в фазе деталей дозаполнит остальное (UPDATE по id).
    """
    if not anno_id:
        return
    existing = session.get(Announcement, anno_id)
    if existing is None:
        session.add(Announcement(id=anno_id, url=url))
        session.flush()


def _get_or_insert_lot(session: Session, hit: ListingHit) -> tuple[Lot, bool]:
    """Вернуть (lot, created). Гонку insert'а (параллельный прогон — daily vs
    /scan с пересекающимися kato — вставил тот же лот) переживаем через savepoint:
    на Postgres второй INSERT c тем же PK даёт IntegrityError, и без изоляции он
    откатывал бы ВЕСЬ оставшийся Phase-1 листинг (P0 №4). begin_nested откатывает
    только эту вставку, после чего продолжаем как update существующего лота.
    """
    lot = session.get(Lot, hit.lot_id)
    if lot is not None:
        return lot, False
    for _ in range(2):
        try:
            with session.begin_nested():
                # FK на announcements строгий в Postgres — родитель ДО лота.
                _ensure_announcement_stub(session, hit.announcement_id, hit.announcement_url)
                lot = Lot(id=hit.lot_id, url=hit.announcement_url)
                session.add(lot)
                session.flush()
            return lot, True
        except IntegrityError:
            existing = session.get(Lot, hit.lot_id)
            if existing is not None:
                return existing, False
            # Конфликт был только по stub объявления — теперь он есть, повторяем.
    raise RuntimeError(f"upsert лота {hit.lot_id}: не удалось вставить после гонки")


def _upsert_lot_from_listing(
    session: Session,
    hit: ListingHit,
    kato: str,
    *,
    on_new: list[ListingHit],
    on_status_change: list[tuple[ListingHit, int | None]],
) -> Lot:
    lot, is_new = _get_or_insert_lot(session, hit)
    if is_new:
        on_new.append(hit)

    prev_status = lot.status_code
    new_status_name = hit.status_name or lot.status_name
    new_status_code = _status_code_from_name(new_status_name)
    if new_status_name and new_status_code is None:
        # Неизвестный статус → is_actual=False молча гасит лот (пропадёт из
        # /actual, матчинга, уведомлений). Раньше это было тихо; теперь активный
        # сигнал (WARNING уходит в Sentry) — значит goszakup ввёл новый статус
        # и STATUS_NAMES пора обновить (P0 №6).
        _warn_unknown_status(new_status_name)

    customer = _get_or_create_org(session, bin_=None, name=hit.customer_name)

    lot.number = hit.lot_number or lot.number
    lot.announcement_id = hit.announcement_id
    lot.customer_id = customer.id if customer else lot.customer_id
    lot.enstru = hit.enstru or lot.enstru
    lot.name = hit.lot_name or lot.name
    lot.plan_amount = hit.plan_amount if hit.plan_amount is not None else lot.plan_amount
    lot.status_code = new_status_code
    lot.status_name = new_status_name
    lot.is_actual = is_actual(new_status_code)
    lot.method = hit.method or lot.method
    # Пустой kato не затирает регион: общереспубликанские проходы (api-daily,
    # /scan «весь РК») идут без региона, а Lot.kato держит persona-scope —
    # лот без региона невидим пользователям с регионами.
    lot.kato = kato or lot.kato
    lot.url = hit.announcement_url or lot.url
    # API-листинг несёт цифровой код ЕНС ТРУ; не затираем добытое деталями.
    lot.enstru_code = hit.enstru_code or lot.enstru_code
    if not lot.category:
        # Интерим фазы A (WP3): пайплайн ещё фильтрует IT, значение — слаг.
        # WP5 заменит на classify_vertical по всем вертикалям.
        lot.category = "it" if classify(lot.enstru, lot.name) else None
    session.flush()

    if is_new or prev_status != new_status_code:
        session.add(
            LotStatusHistory(
                lot_id=lot.id,
                status_code=new_status_code,
                status_name=new_status_name,
            )
        )
        if not is_new:
            on_status_change.append((hit, prev_status))
    return lot


def _apply_details(session: Session, lot: Lot, detail: AnnouncementDetail) -> None:
    """Дополняет лот данными из детальной страницы."""
    matched = None
    for ld in detail.lots:
        if ld.number and lot.number and ld.number == lot.number:
            matched = ld
            break
    if matched is None and detail.lots:
        matched = detail.lots[0]
    if matched is None:
        return
    if matched.customer_bin:
        cust = _get_or_create_org(
            session, bin_=matched.customer_bin, name=matched.customer_name
        )
        if cust:
            lot.customer_id = cust.id
    lot.extra = matched.extra or lot.extra
    lot.enstru_code = matched.enstru_code or lot.enstru_code
    lot.price_per_unit = matched.price_per_unit or lot.price_per_unit
    lot.quantity = matched.quantity if matched.quantity is not None else lot.quantity
    lot.unit = matched.unit or lot.unit
    lot.amount_y1 = matched.amount_y1 or lot.amount_y1
    lot.amount_y2 = matched.amount_y2 or lot.amount_y2
    lot.amount_y3 = matched.amount_y3 or lot.amount_y3
    for w in detail.winners:
        if w.lot_number and lot.number and w.lot_number == lot.number:
            lot.winner_bin = w.winner_bin or lot.winner_bin
            lot.winner_name = w.winner_name or lot.winner_name
            break
    session.flush()


def _apply_enstru_code(session: Session, lot: Lot, source: DataSource) -> None:
    """Цифровой «Код ТРУ» на HTML-пути доступен только на карточке лота —
    +1 запрос/лот. Тянем поэтому лишь для IT-лотов (как и LLM-шаг) и только
    если ещё нет (API-источник обычно приносит код прямо в детали, см.
    _apply_details). Сбой не должен ронять прогон объявления — деградируем
    тихо (правило #7)."""
    if not lot.category or lot.enstru_code:
        return
    try:
        code = source.fetch_enstru_code(lot.announcement_id, lot.id)
    except Exception as e:
        log.warning("enstru_code fetch failed for lot=%s: %s", lot.id, e)
        return
    if code:
        lot.enstru_code = code
        session.flush()


def _save_documents(
    session: Session,
    announcement: Announcement,
    detail: AnnouncementDetail,
    source: DataSource,
) -> int:
    new_count = 0
    for d in detail.documents:
        if d.direct_url:
            if _save_one_document(
                session, announcement, d.name, d.attribute, d.direct_url, source
            ):
                new_count += 1
            continue
        # Документ за «Перейти»: качаем ТОЛЬКО кандидатов в ТЗ. Остальное
        # (договор, квал. формы, приложения 1..N) намеренно пропускаем —
        # они нам не нужны и лишняя нагрузка на goszakup/v3bl.
        if d.file_type_id is None:
            continue
        if not is_tz_like_name(f"{d.name or ''} {d.attribute or ''}"):
            continue
        try:
            modal_files = source.fetch_modal_files(announcement.id, d.file_type_id)
        except Exception as e:  # на случай неожиданного формата ответа
            log.warning(
                "modal failed for anno=%s type=%s: %s",
                announcement.id, d.file_type_id, e,
            )
            continue
        for mf in modal_files:
            # Имя строки приложения сохраняем как Document.name (так
            # UI показывает «Приложение 16 (Техническая спецификация…)»),
            # а имя файла на диске берётся из mf.name через suggested_name.
            display_name = (
                f"{d.name} · {mf.name}" if len(modal_files) > 1 else d.name
            )
            if _save_one_document(
                session, announcement, display_name, d.attribute, mf.url, source,
                suggested_name=mf.name,
            ):
                new_count += 1
    return new_count


def _save_one_document(
    session: Session,
    announcement: Announcement,
    name: str,
    attribute: str | None,
    url: str,
    source: DataSource,
    *,
    suggested_name: str | None = None,
) -> bool:
    existing = session.scalar(
        select(Document).where(
            Document.announcement_id == announcement.id,
            Document.url == url,
        )
    )
    if existing is not None and existing.local_path:
        return False  # уже скачан — повторно не дёргаем
    if existing is not None:
        # Запись была, но файл не скачался (download_error). Пробуем ещё раз —
        # это закрывает долги после смены инфраструктуры (новый прокси/IP).
        doc = existing
    else:
        doc = Document(
            announcement_id=announcement.id,
            name=name,
            attribute=attribute,
            url=url,
        )
        session.add(doc)
        session.flush()
    res = source.download(announcement.id, url, suggested_name=suggested_name)
    if res.ok:
        doc.local_path = res.local_path
        doc.sha256 = res.sha256
        doc.size = res.size
        doc.content_type = res.content_type
        doc.downloaded_at = datetime.now(UTC)
        doc.download_error = None
    else:
        doc.download_error = res.error
    session.flush()
    return res.ok


def _save_contracts(
    session: Session, lots_by_number: dict[str, Lot], detail: AnnouncementDetail
) -> None:
    for c in detail.contracts:
        lot = lots_by_number.get(c.lot_number)
        if lot is None:
            continue
        supplier = _get_or_create_org(
            session, bin_=c.supplier_bin, name=c.supplier_name
        )
        existing = session.scalar(
            select(Contract).where(
                Contract.lot_id == lot.id,
                Contract.contract_number == c.contract_number,
            )
        )
        if existing is None:
            session.add(
                Contract(
                    lot_id=lot.id,
                    contract_number=c.contract_number,
                    status=c.status,
                    plan_amount=c.plan_amount,
                    contract_amount=c.contract_amount,
                    fact_amount=c.fact_amount,
                    supplier_id=supplier.id if supplier else None,
                    supplier_status=c.supplier_status,
                )
            )
        else:
            existing.status = c.status or existing.status
            existing.plan_amount = c.plan_amount or existing.plan_amount
            existing.contract_amount = c.contract_amount or existing.contract_amount
            existing.fact_amount = c.fact_amount or existing.fact_amount
            existing.supplier_id = (
                supplier.id if supplier else existing.supplier_id
            )
            existing.supplier_status = c.supplier_status or existing.supplier_status


def _status_code_from_name(name: str | None) -> int | None:
    if not name:
        return None
    from ..scraper.statuses import STATUS_NAMES

    for code, n in STATUS_NAMES.items():
        if n == name:
            return code
    return None


@lru_cache(maxsize=256)
def _warn_unknown_status(name: str) -> None:
    """Один WARNING на каждое новое неизвестное имя статуса за жизнь процесса
    (lru_cache дедупит — иначе спам на каждый лот с этим статусом)."""
    log.warning(
        "неизвестный статус goszakup %r — лот помечен НЕактуальным; "
        "обновите scraper/statuses.py:STATUS_NAMES",
        name,
    )


@dataclass
class RunStats:
    listing_count: int = 0
    new_lots: int = 0
    updated_lots: int = 0
    details_fetched: int = 0
    new_documents: int = 0
    llm_analyzed: int = 0
    errors: int = 0

    def add(self, other: RunStats) -> None:
        self.listing_count += other.listing_count
        self.new_lots += other.new_lots
        self.updated_lots += other.updated_lots
        self.details_fetched += other.details_fetched
        self.new_documents += other.new_documents
        self.llm_analyzed += other.llm_analyzed
        self.errors += other.errors


def execute_search(
    session: Session,
    source: DataSource,
    params: SearchParams,
    *,
    categories: list[str] | None = None,
    download_docs: bool = True,
    run_llm: bool = True,
    max_pages: int | None = None,
    listing_only: bool = False,
) -> RunStats:
    """Одна фаза listing+details в уже открытой сессии. Возвращает счётчики.

    Не управляет жизненным циклом ScrapeRun — это ответственность вызывающей
    стороны (run_preset / run_ad_hoc_ingest). Так одна общая ScrapeRun может
    агрегировать несколько подряд идущих SearchParams (например, цикл по
    годам).
    """
    stats = RunStats()
    new_hits: list[ListingHit] = []
    status_changes: list[tuple[ListingHit, int | None]] = []

    # Phase 1: listing + upsert lot stubs
    try:
        for hit in source.iter_listing(params, max_pages=max_pages):
            stats.listing_count += 1
            # Проект только про IT: не-IT лоты не храним и не парсим вообще.
            # Интерим фазы A (WP3): сравнение уже в слагах вертикалей.
            cat = "it" if classify(hit.enstru, hit.lot_name) else None
            if cat is None:
                continue
            if categories and cat not in categories:
                continue
            _upsert_lot_from_listing(
                session,
                hit,
                kato=params.kato,
                on_new=new_hits,
                on_status_change=status_changes,
            )
            if stats.listing_count % 100 == 0:
                session.commit()
        session.commit()
    except Exception as e:
        stats.errors += 1
        log.exception("listing failed: %s", e)
        session.rollback()

    stats.new_lots = len(new_hits)
    stats.updated_lots = len(status_changes)

    if listing_only:
        return stats

    # Phase 2: детали только для тех, кого мы выбрали (новые + сменившие статус)
    targets = {h.announcement_id: h for h in new_hits}
    for h, _prev in status_changes:
        targets.setdefault(h.announcement_id, h)

    for anno_id in list(targets.keys()):
        try:
            detail = source.fetch_announcement(anno_id)
            stats.details_fetched += 1
            anno = _save_announcement(session, detail)

            lots = session.scalars(
                select(Lot).where(Lot.announcement_id == anno.id)
            ).all()
            lots_by_number = {lt.number: lt for lt in lots if lt.number}
            for lot in lots:
                _apply_details(session, lot, detail)
                _apply_enstru_code(session, lot, source)
            _save_contracts(session, lots_by_number, detail)

            if download_docs:
                stats.new_documents += _save_documents(session, anno, detail, source)
            session.commit()

            if run_llm:
                # LLM-классификация только для лотов, прошедших IT-фильтр.
                # Никаких goszakup-запросов отсюда — используем уже скачанные
                # документы. Любая ошибка LLM не должна валить прогон.
                analyzed: list[Lot] = []
                for lot in lots:
                    if not lot.category:
                        continue
                    if analyze_and_save(session, lot):
                        stats.llm_analyzed += 1
                        analyzed.append(lot)
                session.commit()
                # Fan-out матчинга — после commit, чтобы воркер увидел свежий
                # анализ. Sync-режим может жить без Redis — прогон не валим.
                for lot in analyzed:
                    try:
                        from ..queue.matching import enqueue_matches_for_lot

                        enqueue_matches_for_lot(session, lot)
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            "matching fan-out для лота %s не поставлен: %s", lot.id, e
                        )
        except Exception as e:
            stats.errors += 1
            log.exception("anno %s details failed: %s", anno_id, e)
            session.rollback()

    return stats


def run_preset(
    *,
    preset: Preset | None = None,
    params: SearchParams | None = None,
    download_docs: bool = True,
    run_llm: bool = True,
    max_pages: int | None = None,
    categories: list[str] | None = None,
    listing_only: bool = False,
) -> ScrapeRun:
    """Прогоняет один preset. Если передан preset — берёт фильтры из БД."""
    init_db()
    if params is None:
        if preset is None:
            raise ValueError("preset или params обязателен")
        params = SearchParams(
            kato=preset.kato or "",
            amount_from=preset.amount_from or 500_000,
            amount_to=preset.amount_to,
            status_codes=list(preset.status_codes or []),
        )
    if categories is None and preset is not None:
        categories = list(preset.categories or [])

    source = make_source()

    with SessionLocal() as session:
        run = ScrapeRun(preset_id=preset.id if preset else None)
        session.add(run)
        session.commit()
        run_id = run.id

        stats = execute_search(
            session, source, params,
            categories=categories,
            download_docs=download_docs,
            run_llm=run_llm,
            max_pages=max_pages,
            listing_only=listing_only,
        )

        run = session.get(ScrapeRun, run_id)
        run.finished_at = datetime.now(UTC)
        run.listing_count = stats.listing_count
        run.details_fetched = stats.details_fetched
        run.new_lots = stats.new_lots
        run.updated_lots = stats.updated_lots
        run.new_documents = stats.new_documents
        run.llm_analyzed = stats.llm_analyzed
        run.errors = stats.errors
        session.commit()
        log.info(
            "run done: listing=%d new=%d changed=%d details=%d docs=%d llm=%d errors=%d",
            stats.listing_count, stats.new_lots, stats.updated_lots,
            stats.details_fetched, stats.new_documents, stats.llm_analyzed,
            stats.errors,
        )
        return run
