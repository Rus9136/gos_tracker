"""ОКЭД закупающих организаций из реестра участников OWS (Subjects).

Тот же запрос, что у `supplier_contacts` (контакты приезжают заодно и
применяются), но выборка другая: заказчики и организаторы с БИН, у которых
реестр ещё не опрашивался. Серверного фильтра по ОКЭД в `Subjects` нет —
один запрос на организацию при ~1 rps, поэтому команда ad-hoc и с потолком.

Отметка `oked_synced_at` ставится и при пустом ответе (паттерн
`bids_synced_at`, правило #22): без неё организации, которых в реестре нет,
навсегда занимали бы верх выборки.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.client import OwsApiError, OwsClient
from ..db.models import Lot, Organization
from ..industries import classify_industry
from ..orgs import buyer_condition
from .supplier_contacts import apply_subject, fetch_subject

log = logging.getLogger(__name__)


@dataclass
class IndustrySyncStats:
    processed: int = 0
    found: int = 0
    not_found: int = 0
    errors: int = 0
    classified: int = 0


def orgs_to_sync(session: Session, *, limit: int = 500) -> list[Organization]:
    """Закупающие с БИН без опроса реестра, крупные (по числу лотов) первыми."""
    lots_cnt = (
        select(func.count(Lot.id))
        .where(Lot.customer_id == Organization.id)
        .correlate(Organization)
        .scalar_subquery()
    )
    q = (
        select(Organization)
        .where(
            Organization.bin.is_not(None),
            Organization.oked_synced_at.is_(None),
            buyer_condition(),
        )
        .order_by(lots_cnt.desc(), Organization.id)
        .limit(limit)
    )
    return list(session.scalars(q))


def apply_oked(org: Organization, subject: dict) -> bool:
    """ОКЭД из реестра и отрасль по нему — только если ещё не присвоена."""
    raw = subject.get("okedList")
    code = str(raw).strip() if raw not in (None, "") else ""
    changed = False
    if code and code != org.oked:
        org.oked = code[:10]
        changed = True
    if org.industry is None:
        slug = classify_industry(org.name, org.oked)
        if slug:
            org.industry = slug
            changed = True
    return changed


def sync_industries(
    session: Session, client: OwsClient, *, limit: int = 500, on_progress=None
) -> IndustrySyncStats:
    stats = IndustrySyncStats()
    orgs = orgs_to_sync(session, limit=limit)
    log.info("industry-sync: организаций к опросу %d", len(orgs))
    for org in orgs:
        now = datetime.now(UTC)
        try:
            subject = fetch_subject(client, org.bin)
        except OwsApiError as e:
            log.warning("industry-sync: %s: %s", org.bin, e)
            session.rollback()
            stats.errors += 1
            continue
        if subject is None:
            stats.not_found += 1
        else:
            stats.found += 1
            apply_subject(org, subject)
            # Контакты приехали тем же ответом — отметка contacts_synced_at
            # честная, contacts-sync эту организацию заново не дёрнет.
            org.contacts_synced_at = now
            if apply_oked(org, subject) and org.industry:
                stats.classified += 1
        org.oked_synced_at = now
        stats.processed += 1
        session.commit()
        if on_progress is not None:
            on_progress(stats)
    return stats
