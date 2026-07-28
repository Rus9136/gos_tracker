"""Отчёт по поставщикам: кто выигрывает и кто проигрывает по кодам ЕНС ТРУ.

Лидогенерация: отбираем поставщиков, играющих в закупках нашего профиля,
и берём их контакты (обогащаются из реестра участников OWS —
jobs/supplier_contacts.py). К goszakup НЕ ходит — чистый SQL по БД.

Три источника с разной полнотой (см. правило #22):
- победы — `lots.winner_bin` (самый полный: HTML + договоры + заявки);
- сумма победы — договор, при его отсутствии плановая сумма лота;
- вторые места и участия — `lot_bids` (только объявления, опрошенные
  bids-sync после дедлайна; у старых лотов заявок в БД нет — «0 участий»
  значит «нет данных», а не «не участвовал»).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Announcement, Contract, Lot, LotBid, Organization

SECOND_STATUS = "Второй победитель"


@dataclass
class SupplierFilters:
    enstru_code: str = ""  # префикс цифрового кода ЕНС ТРУ (192021.530…)
    enstru: str = ""  # подстрока имени ЕНС ТРУ («компьютер», «интернет»)
    category: str = ""
    year: int = 0  # год публикации объявления, 0 = всё время


@dataclass
class SupplierRow:
    bin: str
    name: str = ""
    wins: int = 0
    won_total: float = 0.0
    seconds: int = 0
    bids: int = 0
    org_id: int | None = None
    email: str = ""
    phone: str = ""
    website: str = ""
    address: str = ""


def _lot_conditions(f: SupplierFilters) -> list:
    conds = []
    if f.enstru_code:
        conds.append(Lot.enstru_code.like(f"{f.enstru_code.strip()}%"))
    if f.enstru:
        conds.append(Lot.enstru.ilike(f"%{f.enstru.strip()}%"))
    if f.category:
        conds.append(Lot.category == f.category)
    return conds


def build_supplier_report(
    session: Session, f: SupplierFilters, *, limit: int = 300
) -> list[SupplierRow]:
    conds = _lot_conditions(f)
    year_join = bool(f.year)

    contract_sq = (
        select(func.max(Contract.contract_amount))
        .where(Contract.lot_id == Lot.id)
        .scalar_subquery()
    )
    wins_q = (
        select(
            Lot.winner_bin,
            func.max(Lot.winner_name).label("name"),
            func.count(Lot.id).label("wins"),
            func.coalesce(
                func.sum(func.coalesce(contract_sq, Lot.plan_amount)), 0
            ).label("total"),
        )
        .where(Lot.winner_bin.is_not(None), Lot.winner_bin != "", *conds)
        .group_by(Lot.winner_bin)
    )
    bids_q = (
        select(
            LotBid.supplier_bin,
            func.max(LotBid.supplier_name).label("name"),
            func.count(LotBid.id).label("bids"),
            func.count(LotBid.id)
            .filter(LotBid.status == SECOND_STATUS)
            .label("seconds"),
        )
        .join(Lot, Lot.id == LotBid.lot_id)
        .where(LotBid.supplier_bin.is_not(None), LotBid.supplier_bin != "", *conds)
        .group_by(LotBid.supplier_bin)
    )
    if year_join:
        year_cond = func.extract("year", Announcement.publish_date) == f.year
        wins_q = wins_q.join(
            Announcement, Announcement.id == Lot.announcement_id
        ).where(year_cond)
        bids_q = bids_q.join(
            Announcement, Announcement.id == Lot.announcement_id
        ).where(year_cond)

    rows: dict[str, SupplierRow] = {}
    for bin_, name, wins, total in session.execute(wins_q):
        rows[bin_] = SupplierRow(
            bin=bin_, name=name or "", wins=wins, won_total=float(total or 0)
        )
    for bin_, name, bids, seconds in session.execute(bids_q):
        row = rows.setdefault(bin_, SupplierRow(bin=bin_))
        row.name = row.name or name or ""
        row.bids = bids
        row.seconds = seconds

    orgs = session.execute(
        select(
            Organization.bin,
            Organization.id,
            Organization.name,
            Organization.email,
            Organization.phone,
            Organization.website,
            Organization.address,
        ).where(Organization.bin.in_(rows))
    )
    for bin_, org_id, name, email, phone, website, address in orgs:
        row = rows[bin_]
        row.org_id = org_id
        row.name = row.name or name or ""
        row.email = email or ""
        row.phone = phone or ""
        row.website = website or ""
        row.address = address or ""

    ordered = sorted(
        rows.values(), key=lambda r: (r.wins, r.won_total, r.bids), reverse=True
    )
    return ordered[:limit]


def render_csv(rows: list[SupplierRow]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            "БИН/ИИН",
            "Поставщик",
            "Побед",
            "Сумма побед, тг",
            "Вторых мест",
            "Заявок",
            "Телефон",
            "Email",
            "Сайт",
            "Адрес",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r.bin,
                r.name,
                r.wins,
                round(r.won_total),
                r.seconds,
                r.bids,
                r.phone,
                r.email,
                r.website,
                r.address,
            ]
        )
    return buf.getvalue()
