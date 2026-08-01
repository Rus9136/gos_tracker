"""Маппинг JSON OWS → dataclasses скрейпера (ListingHit, AnnouncementDetail).

Даты API — алматинское время UTC+5 без зоны (сверено с боевым HTML-парсером,
NOTES.md п.6): дедлайн конвертируем в UTC как в announce.py, publish_date
храним наивным — как хранит HTML-путь.

Наименование ЕНС ТРУ: у лота на goszakup nameRu и есть наименование позиции
ЕНС ТРУ; Plans[].RefEnstru даёт его вместе с цифровым кодом, но у свежих ЗЦП
план часто не проиндексирован — тогда берём nameRu, а код остаётся пустым
(NOTES.md п.2).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..config import announcement_url
from ..scraper.announce import AnnouncementDetail, DocumentRow, LotDetail
from ..scraper.katos import REGIONS
from ..scraper.modal_files import is_tz_like_name
from ..scraper.search import ListingHit
from ..scraper.statuses import STATUS_NAMES

log = logging.getLogger(__name__)

_ALMATY_TZ = timezone(timedelta(hours=5))


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def almaty_to_utc(s: str | None) -> datetime | None:
    d = _parse_dt(s)
    if d is None:
        return None
    return d.replace(tzinfo=_ALMATY_TZ).astimezone(timezone.utc)


def utc_to_almaty_str(dt: datetime) -> str:
    """UTC → строка фильтра OWS (lastUpdateDate и пр. — алматинское время)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ALMATY_TZ).strftime("%Y-%m-%d %H:%M:%S")


def region_prefix(region_code: str) -> str:
    return region_code[:2]


# 2-значные префиксы всех 20 регионов уникальны (см. test_api_mapping).
_REGION_BY_PREFIX: dict[str, str] = {r.code[:2]: r.code for r in REGIONS}


def region_from_kato_list(
    kato_list: list[str] | None, anno_kato_list: list[str] | None = None
) -> str:
    """КАТО точек поставки → код региона katos.REGIONS.

    Лотовый список приоритетнее объявленческого (у ~9% лотов он пуст в
    индексе — NOTES.md). Несколько регионов → первый совпавший
    (детерминированно). Не резолвится → '' — тогда апсерт не затирает
    прежний регион (см. _upsert_lot_from_listing).
    """
    for k in [*(kato_list or []), *(anno_kato_list or [])]:
        code = _REGION_BY_PREFIX.get((k or "")[:2])
        if code:
            return code
    return ""


def kato_in_region(kato_list: list[str] | None, region_code: str) -> bool:
    """Регион в API не фильтруется на сервере (kato — только точные коды),
    поэтому сверка по 2-значному префиксу: у всех 20 регионов (katos.REGIONS)
    префиксы уникальны."""
    if not region_code:
        return True
    prefix = region_prefix(region_code)
    return any((k or "").startswith(prefix) for k in kato_list or [])


def _lot_status_name(ref_lot_status_id: int | None) -> str:
    name = STATUS_NAMES.get(ref_lot_status_id or 0)
    if name is None:
        log.warning("OWS: неизвестный refLotStatusId=%s", ref_lot_status_id)
        return ""
    return name


def _lot_enstru(lot: dict) -> tuple[str, str | None]:
    """(наименование ЕНС ТРУ, цифровой код | None)."""
    for plan in lot.get("Plans") or []:
        ref = plan.get("RefEnstru") or {}
        if ref.get("code"):
            return ref.get("nameRu") or lot.get("nameRu") or "", ref["code"]
    return lot.get("nameRu") or "", None


def listing_hit_from_lot(lot: dict) -> ListingHit:
    enstru_name, enstru_code = _lot_enstru(lot)
    anno_id = int(lot.get("trdBuyId") or 0)
    amount = lot.get("amount")
    return ListingHit(
        lot_id=int(lot["id"]),
        lot_number=lot.get("lotNumber") or "",
        announcement_id=anno_id,
        announcement_number=lot.get("trdBuyNumberAnno") or "",
        announcement_url=announcement_url(anno_id),
        lot_name=lot.get("nameRu") or "",
        customer_name=lot.get("customerNameRu") or "",
        enstru=enstru_name,
        quantity=str(lot.get("count") or ""),
        plan_amount=float(amount) if amount is not None else None,
        amount_raw=str(amount or ""),
        method=(lot.get("RefTradeMethods") or {}).get("nameRu") or "",
        status_name=_lot_status_name(lot.get("refLotStatusId")),
        trd_buy_id=str(anno_id or ""),
        enstru_code=enstru_code or "",
    )


def _lot_detail_from_lot(lot: dict) -> LotDetail:
    enstru_name, enstru_code = _lot_enstru(lot)
    amount = lot.get("amount")
    count = lot.get("count")
    price = None
    if amount is not None and count:
        price = round(float(amount) / float(count), 2)
    return LotDetail(
        number=lot.get("lotNumber") or "",
        customer_bin=lot.get("customerBin") or "",
        customer_name=lot.get("customerNameRu") or "",
        enstru=enstru_name,
        name=lot.get("nameRu") or "",
        extra=lot.get("descriptionRu") or "",
        price_per_unit=price,
        quantity=float(count) if count is not None else None,
        # Единицы измерения в API-выдаче нет; пустое значение не затирает
        # добытое HTML-ом (см. _apply_details, обновление через `or`).
        unit="",
        plan_amount=float(amount) if amount is not None else None,
        amount_y1=None,
        amount_y2=None,
        amount_y3=None,
        status_name=_lot_status_name(lot.get("refLotStatusId")),
        enstru_code=enstru_code,
    )


def _document_row(f: dict) -> DocumentRow:
    name = f.get("nameRu") or f.get("originalName") or ""
    # Правило #3: качаем только кандидатов в ТЗ. Прочие строки остаются без
    # ссылки и без file_type_id — _save_documents их молча пропускает.
    tz_like = is_tz_like_name(f"{f.get('nameRu') or ''} {f.get('originalName') or ''}")
    return DocumentRow(
        name=name,
        attribute="",
        file_type_id=None,
        direct_url=(f.get("filePath") or None) if tz_like else None,
        suggested_name=f.get("originalName") or None,
    )


def detail_from_trd_buy(tb: dict) -> AnnouncementDetail:
    anno_id = int(tb["id"])
    return AnnouncementDetail(
        id=anno_id,
        url=announcement_url(anno_id),
        number=tb.get("numberAnno") or "",
        method=(tb.get("RefTradeMethods") or {}).get("nameRu") or "",
        purchase_type=(tb.get("RefTypeTrade") or {}).get("nameRu") or "",
        subject_type=(tb.get("RefSubjectType") or {}).get("nameRu") or "",
        organizer_bin=tb.get("orgBin") or "",
        organizer_name=tb.get("orgNameRu") or "",
        organizer_address="",
        lots_count=tb.get("countLots"),
        total_amount=float(tb["totalSum"]) if tb.get("totalSum") is not None else None,
        attributes="",
        publish_date=_parse_dt(tb.get("publishDate")),
        application_start=almaty_to_utc(tb.get("startDate")),
        application_end=almaty_to_utc(tb.get("endDate")),
        lots=[_lot_detail_from_lot(lot) for lot in tb.get("Lots") or []],
        documents=[_document_row(f) for f in tb.get("Files") or []],
    )
