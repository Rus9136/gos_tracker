"""Синк договоров и победителей из GraphQL Contract (OWS).

Закрывает две дыры разом: (1) на API-пути detail-фаза не приносит
contracts/winners (HTML-табы не читаются), (2) закрытые лоты не
пересканируются (правило #5) и их договоры раньше оставались пустыми.
Синк идёт по окну lastUpdateDate договора и обновляет ТОЛЬКО лоты, уже
существующие в БД, — включая закрытые; чужие лоты РК (~97% потока)
отбрасываются батч-фильтром по lots.id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.client import OwsClient
from ..api.mapping import utc_to_almaty_str
from ..api.queries import CONTRACTS_QUERY
from ..api.refs import ref_items
from ..db.models import Contract, Lot
from .run_preset import _get_or_create_org

log = logging.getLogger(__name__)


@dataclass
class ContractsSyncStats:
    scanned: int = 0
    matched: int = 0
    created: int = 0
    updated: int = 0
    winners_filled: int = 0


def _status_name(client: OwsClient, status_id: int | None) -> str:
    if not status_id:
        return ""
    item = ref_items(client, "ref_contract_status").get(int(status_id))
    if item is None:
        log.warning("contracts-sync: неизвестный ref_contract_status id=%s", status_id)
        return ""
    return item.get("name_ru") or ""


def _num(v) -> float | None:
    return float(v) if v is not None else None


def upsert_contract_from_api(
    session: Session,
    lot: Lot,
    c: dict,
    unit: dict,
    status_name: str,
) -> str | None:
    """Возвращает 'created' / 'updated' / None (без изменений).

    Ключ — (lot_id, contract_number); семантика обновления та же, что у
    HTML-пути _save_contracts: пустое значение не затирает добытое.
    fact_amount берём только у однолотового договора — у мультилотового
    faktSum неатрибутируем конкретному лоту.
    """
    number = c.get("contractNumber") or c.get("contractNumberSys") or ""
    if not number:
        return None
    single_lot = len(c.get("ContractUnits") or []) == 1
    contract_amount = _num(unit.get("totalSum"))
    if contract_amount is None and single_lot:
        contract_amount = _num(c.get("contractSum"))
    fact_amount = _num(c.get("faktSum")) if single_lot else None

    row = session.scalar(
        select(Contract).where(
            Contract.lot_id == lot.id, Contract.contract_number == number
        )
    )
    created = row is None
    if created:
        row = Contract(lot_id=lot.id, contract_number=number)
        session.add(row)

    before = (row.status, row.contract_amount, row.fact_amount, row.supplier_id)
    row.status = status_name or row.status
    row.contract_amount = (
        contract_amount if contract_amount is not None else row.contract_amount
    )
    row.fact_amount = fact_amount if fact_amount is not None else row.fact_amount
    if c.get("supplierBiin"):
        org = _get_or_create_org(
            session, bin_=c["supplierBiin"], name=c.get("supplierFio") or ""
        )
        if org:
            row.supplier_id = org.id
    session.flush()
    if created:
        return "created"
    after = (row.status, row.contract_amount, row.fact_amount, row.supplier_id)
    return "updated" if after != before else None


def apply_winner_from_contract(lot: Lot, c: dict) -> bool:
    """Победитель = поставщик договора. Не затираем добытое HTML-ом."""
    if lot.winner_bin or not c.get("supplierBiin"):
        return False
    lot.winner_bin = c["supplierBiin"]
    lot.winner_name = lot.winner_name or c.get("supplierFio") or ""
    return True


def sync_contracts(
    session: Session,
    client: OwsClient,
    *,
    dt_from: datetime,
    dt_to: datetime,
    on_progress=None,
) -> ContractsSyncStats:
    stats = ContractsSyncStats()
    variables = {
        "f": {"lastUpdateDate": [utc_to_almaty_str(dt_from), utc_to_almaty_str(dt_to)]}
    }
    batch: list[dict] = []

    def flush_batch() -> None:
        if not batch:
            return
        lot_ids = {
            int(u["lotId"])
            for c in batch
            for u in c.get("ContractUnits") or []
            if u.get("lotId")
        }
        ours = {
            lot.id: lot
            for lot in session.scalars(select(Lot).where(Lot.id.in_(lot_ids)))
        } if lot_ids else {}
        for c in batch:
            status_name = _status_name(client, c.get("refContractStatusId"))
            for u in c.get("ContractUnits") or []:
                lot = ours.get(int(u.get("lotId") or 0))
                if lot is None:
                    continue
                stats.matched += 1
                # Статус юнита точнее общедоговорного (per-lot).
                unit_status = _status_name(client, u.get("refContractStatusId"))
                res = upsert_contract_from_api(
                    session, lot, c, u, unit_status or status_name
                )
                if res == "created":
                    stats.created += 1
                elif res == "updated":
                    stats.updated += 1
                if apply_winner_from_contract(lot, c):
                    stats.winners_filled += 1
        session.commit()
        batch.clear()
        if on_progress is not None:
            on_progress(stats)

    for c in client.iter_graphql(
        CONTRACTS_QUERY, variables, root="Contract", limit=200
    ):
        stats.scanned += 1
        batch.append(c)
        if len(batch) >= 200:
            flush_batch()
    flush_batch()
    return stats
