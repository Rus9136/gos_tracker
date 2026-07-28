"""Обогащение контактов поставщиков из реестра участников OWS (Subjects).

Контакты (email/телефон/сайт) нужны для лидогенерации: отчёт /suppliers
показывает, кто выигрывает и проигрывает по нашим кодам ЕНС ТРУ, — а
связаться с ними без реестра неоткуда: ни листинг, ни детали объявления
контактов поставщика не отдают.

Особенности реестра (проверено вживую 2026-07-28):
- ЮЛ находятся по фильтру `bin`, ИП — ТОЛЬКО по `iin` (их 12-значный
  идентификатор — ИИН). Отличить заранее нельзя, пробуем оба фильтра.
- Покрытие неполное: часть ИП в реестре отсутствует — поэтому отметка
  `contacts_synced_at` ставится и при пустом ответе (паттерн
  `bids_synced_at`, правило #22), иначе такие организации навсегда
  занимали бы верх выборки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.client import OwsApiError, OwsClient
from ..api.queries import SUBJECT_QUERY
from ..db.models import Lot, LotBid, Organization
from .run_preset import _get_or_create_org

log = logging.getLogger(__name__)

# Контакты меняются редко; чаще раза в полгода реестр дёргать незачем.
REFRESH_DAYS = 180


@dataclass
class ContactsSyncStats:
    processed: int = 0
    found: int = 0
    not_found: int = 0
    errors: int = 0


def _supplier_bins(session: Session) -> dict[str, str]:
    """БИН/ИИН → имя всех, кто фигурирует как победитель или участник."""
    result: dict[str, str] = {}
    winners = session.execute(
        select(Lot.winner_bin, func.max(Lot.winner_name))
        .where(Lot.winner_bin.is_not(None), Lot.winner_bin != "")
        .group_by(Lot.winner_bin)
    )
    for bin_, name in winners:
        result[bin_] = name or ""
    bidders = session.execute(
        select(LotBid.supplier_bin, func.max(LotBid.supplier_name))
        .where(LotBid.supplier_bin.is_not(None), LotBid.supplier_bin != "")
        .group_by(LotBid.supplier_bin)
    )
    for bin_, name in bidders:
        result.setdefault(bin_, name or "")
    return result


def orgs_to_sync(
    session: Session, *, refresh_days: int = REFRESH_DAYS, limit: int = 500
) -> list[Organization]:
    """Организации-поставщики без свежих контактов; недостающие создаются.

    Победитель, добытый HTML-ом, попадает только в `lots.winner_bin` —
    строки в organizations у него может не быть, а контакты вешать не на что.
    """
    bins = _supplier_bins(session)
    if not bins:
        return []
    existing = {
        o.bin
        for o in session.scalars(
            select(Organization).where(Organization.bin.in_(bins))
        )
    }
    for bin_, name in bins.items():
        if bin_ not in existing:
            _get_or_create_org(session, bin_=bin_, name=name)
    session.flush()

    cutoff = datetime.now(UTC) - timedelta(days=refresh_days)
    q = (
        select(Organization)
        .where(
            Organization.bin.in_(bins),
            (Organization.contacts_synced_at.is_(None))
            | (Organization.contacts_synced_at < cutoff),
        )
        .order_by(Organization.contacts_synced_at.asc().nulls_first())
        .limit(limit)
    )
    return list(session.scalars(q))


def fetch_subject(client: OwsClient, id_: str) -> dict | None:
    for field in ("bin", "iin"):
        data, _ = client.graphql(SUBJECT_QUERY, {"f": {field: id_}})
        subjects = data.get("Subjects") or []
        if subjects:
            return subjects[0]
    return None


def apply_subject(org: Organization, subject: dict) -> bool:
    """Непустые поля реестра перекрывают наши (реестр свежее), пустые —
    не затирают то, что уже есть.

    Значения режутся под размер колонок: в поле phone реестра встречается
    целый справочник номеров по городам на 260+ символов (Казахтелеком) —
    Postgres на таком падает StringDataRightTruncation."""
    limits = {"email": 200, "phone": 100, "website": 300}
    changed = False
    for attr, key in (("email", "email"), ("phone", "phone"), ("website", "website")):
        value = (subject.get(key) or "").strip()[: limits[attr]]
        if value and value != getattr(org, attr):
            setattr(org, attr, value)
            changed = True
    if not org.address:
        for addr in subject.get("Address") or []:
            text = (addr.get("address") or "").strip()
            if text:
                org.address = text
                changed = True
                break
    return changed


def sync_supplier_contacts(
    session: Session,
    client: OwsClient,
    *,
    refresh_days: int = REFRESH_DAYS,
    limit: int = 500,
    on_progress=None,
) -> ContactsSyncStats:
    stats = ContactsSyncStats()
    orgs = orgs_to_sync(session, refresh_days=refresh_days, limit=limit)
    log.info("contacts-sync: организаций к опросу %d", len(orgs))
    for org in orgs:
        try:
            subject = fetch_subject(client, org.bin)
        except OwsApiError as e:
            # Одна организация не роняет проход; без отметки она вернётся
            # в выборку следующим прогоном.
            log.warning("contacts-sync: %s: %s", org.bin, e)
            session.rollback()
            stats.errors += 1
            continue
        if subject is None:
            stats.not_found += 1
        else:
            stats.found += 1
            apply_subject(org, subject)
        org.contacts_synced_at = datetime.now(UTC)
        stats.processed += 1
        session.commit()
        if on_progress is not None:
            on_progress(stats)
    return stats
