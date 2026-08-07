"""Карточка одного поставщика: что выиграл, где участвовал и по какой цене.

Отчёт `/suppliers` отвечает «кто играет в наших закупках», карточка — «как
именно играет этот»: победы с суммой договора, проигранные заявки со своей
и победившей ценой, разбивка по заказчикам и кодам ЕНС ТРУ.

Победа собирается тремя путями, как и роль в `orgs.py`: `lots.winner_bin`
(HTML-таб winners и заявки), заявка со статусом «Победитель» и FK
`contracts.supplier_id`. Пропуск любого теряет часть побед — у разных лотов
заполнены разные источники.

Полнота у источников разная (правило #22): заявки OWS отдаёт только после
дедлайна, и только по объявлениям, которые опросил bids-sync. Поэтому «0
участий» значит «нет данных», а не «не участвовал», и win-rate считается
ТОЛЬКО по лотам, где заявка этого поставщика в БД есть, — иначе он был бы
тождественно равен 100% (победы известны почти всегда, участия — редко).

Агрегаты считаются по всем лотам, таблицы обрезаются до `ROWS_LIMIT`
свежих: у крупного поставщика побед тысячи, а карточка — не выгрузка.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Announcement, Contract, Lot, LotBid, Organization

WINNER_STATUS = "Победитель"
SECOND_STATUS = "Второй победитель"
REJECTED_STATUS = "Отклонено"
ROWS_LIMIT = 200
TOP_LIMIT = 6


@dataclass
class SupplierLotRow:
    lot_id: int
    number: str = ""
    name: str = ""
    url: str = ""
    customer_name: str = ""
    category: str | None = None
    status_name: str | None = None
    published: datetime | None = None
    # Сумма лота: договор точнее плана, поэтому источник показываем явно —
    # «план» значит «договора в БД нет», а не «купили за столько».
    amount: float = 0.0
    amount_source: str = "plan"
    contract_number: str | None = None
    contract_status: str | None = None
    my_bid: float | None = None
    my_bid_status: str | None = None
    winner_bin: str | None = None
    winner_name: str | None = None
    winner_amount: float | None = None


@dataclass
class SupplierCard:
    bin: str
    name: str = ""
    org: Organization | None = None
    wins_n: int = 0
    won_total: float = 0.0
    bid_lots_n: int = 0
    seconds_n: int = 0
    rejected_n: int = 0
    win_rate: float | None = None
    first_win: datetime | None = None
    last_win: datetime | None = None
    wins: list[SupplierLotRow] = field(default_factory=list)
    losses: list[SupplierLotRow] = field(default_factory=list)
    wins_shown: int = 0
    losses_shown: int = 0
    top_customers: list[tuple[str, int, float]] = field(default_factory=list)
    top_enstru: list[tuple[str, int, float]] = field(default_factory=list)


def _f(value: Decimal | float | None) -> float:
    return float(value) if value is not None else 0.0


def win_lot_ids(session: Session, bin_: str, org_id: int | None) -> set[int]:
    ids = set(
        session.scalars(select(Lot.id).where(Lot.winner_bin == bin_)).all()
    )
    ids |= set(
        session.scalars(
            select(LotBid.lot_id).where(
                LotBid.supplier_bin == bin_, LotBid.status == WINNER_STATUS
            )
        ).all()
    )
    if org_id is not None:
        ids |= set(
            session.scalars(
                select(Contract.lot_id).where(Contract.supplier_id == org_id)
            ).all()
        )
    return ids


def _contract_by_lot(session: Session, lot_ids: list[int]) -> dict[int, tuple]:
    if not lot_ids:
        return {}
    rows = session.execute(
        select(
            Contract.lot_id,
            func.max(Contract.contract_amount),
            func.max(Contract.contract_number),
            func.max(Contract.status),
        )
        .where(Contract.lot_id.in_(lot_ids))
        .group_by(Contract.lot_id)
    )
    return {lot_id: (amount, number, status) for lot_id, amount, number, status in rows}


def _bids_by_lot(session: Session, lot_ids: list[int]) -> dict[int, list[LotBid]]:
    if not lot_ids:
        return {}
    out: dict[int, list[LotBid]] = {}
    for bid in session.scalars(select(LotBid).where(LotBid.lot_id.in_(lot_ids))):
        out.setdefault(bid.lot_id, []).append(bid)
    return out


def _bid_amount(bid: LotBid) -> float | None:
    """Сумма заявки; `price` (за единицу) — последний фолбэк, у части заявок
    API отдаёт только его."""
    for value in (bid.amount, bid.discount_price, bid.price):
        if value is not None:
            return float(value)
    return None


def build_supplier_card(
    session: Session, bin_: str, *, rows_limit: int = ROWS_LIMIT
) -> SupplierCard:
    card = SupplierCard(bin=bin_)
    card.org = session.scalars(
        select(Organization).where(Organization.bin == bin_)
    ).first()
    org_id = card.org.id if card.org else None

    wins = win_lot_ids(session, bin_, org_id)
    my_bids = {
        bid.lot_id: bid
        for bid in session.scalars(
            select(LotBid).where(LotBid.supplier_bin == bin_)
        )
    }
    card.bid_lots_n = len(my_bids)
    card.seconds_n = sum(1 for b in my_bids.values() if b.status == SECOND_STATUS)
    card.rejected_n = sum(1 for b in my_bids.values() if b.status == REJECTED_STATUS)
    card.wins_n = len(wins)
    # Знаменатель — только лоты с известной заявкой: победы видны почти
    # всегда, участия — лишь по опрошенным после дедлайна объявлениям.
    if my_bids:
        card.win_rate = round(100 * len(wins & set(my_bids)) / len(my_bids), 1)

    losses = set(my_bids) - wins
    lot_ids = sorted(wins | losses)
    if not lot_ids:
        card.name = (card.org.name if card.org else "") or bin_
        return card

    # Сумма побед и разбивки — по всем лотам, не по обрезанной таблице.
    contract_sq = (
        select(func.max(Contract.contract_amount))
        .where(Contract.lot_id == Lot.id)
        .scalar_subquery()
    )
    amount_expr = func.coalesce(contract_sq, Lot.plan_amount)
    if wins:
        card.won_total = _f(
            session.scalar(
                select(func.coalesce(func.sum(amount_expr), 0)).where(
                    Lot.id.in_(wins)
                )
            )
        )
        card.top_customers = [
            (name or "— без имени —", n, _f(total))
            for name, n, total in session.execute(
                select(
                    Organization.name,
                    func.count(Lot.id),
                    func.coalesce(func.sum(amount_expr), 0),
                )
                .join(Organization, Organization.id == Lot.customer_id, isouter=True)
                .where(Lot.id.in_(wins))
                .group_by(Organization.name)
                .order_by(func.count(Lot.id).desc())
                .limit(TOP_LIMIT)
            )
        ]
        card.top_enstru = [
            (name or "— без ЕНС ТРУ —", n, _f(total))
            for name, n, total in session.execute(
                select(
                    Lot.enstru,
                    func.count(Lot.id),
                    func.coalesce(func.sum(amount_expr), 0),
                )
                .where(Lot.id.in_(wins))
                .group_by(Lot.enstru)
                .order_by(func.count(Lot.id).desc())
                .limit(TOP_LIMIT)
            )
        ]
        first_last = session.execute(
            select(
                func.min(Announcement.publish_date),
                func.max(Announcement.publish_date),
            )
            .select_from(Lot)
            .join(Announcement, Announcement.id == Lot.announcement_id)
            .where(Lot.id.in_(wins))
        ).one()
        card.first_win, card.last_win = first_last

    # Детали — только для строк таблиц: свежие сверху, остальное обрезаем.
    shown = sorted(
        session.execute(
            select(Lot.id, Announcement.publish_date)
            .join(Announcement, Announcement.id == Lot.announcement_id, isouter=True)
            .where(Lot.id.in_(lot_ids))
        ).all(),
        key=lambda r: (r[1] is not None, r[1]),
        reverse=True,
    )
    win_ids_shown = [i for i, _ in shown if i in wins][:rows_limit]
    loss_ids_shown = [i for i, _ in shown if i in losses][:rows_limit]
    detail_ids = win_ids_shown + loss_ids_shown

    contracts = _contract_by_lot(session, detail_ids)
    lot_bids = _bids_by_lot(session, detail_ids)
    lots = {
        lot.id: lot
        for lot in session.scalars(
            select(Lot).where(Lot.id.in_(detail_ids))
        )
    }
    annos = {
        a.id: a
        for a in session.scalars(
            select(Announcement).where(
                Announcement.id.in_([lot.announcement_id for lot in lots.values()])
            )
        )
    }
    customers = {
        o.id: o
        for o in session.scalars(
            select(Organization).where(
                Organization.id.in_(
                    [lot.customer_id for lot in lots.values() if lot.customer_id]
                )
            )
        )
    }

    def _row(lot_id: int) -> SupplierLotRow:
        lot = lots[lot_id]
        anno = annos.get(lot.announcement_id)
        customer = customers.get(lot.customer_id) if lot.customer_id else None
        c_amount, c_number, c_status = contracts.get(lot_id, (None, None, None))
        row = SupplierLotRow(
            lot_id=lot.id,
            number=lot.number or "",
            name=lot.name or "",
            url=lot.url or "",
            customer_name=(customer.name if customer else "") or "",
            category=lot.category,
            status_name=lot.status_name,
            published=anno.publish_date if anno else None,
            contract_number=c_number,
            contract_status=c_status,
            winner_bin=lot.winner_bin,
            winner_name=lot.winner_name,
        )
        if c_amount is not None:
            row.amount, row.amount_source = _f(c_amount), "contract"
        else:
            row.amount, row.amount_source = _f(lot.plan_amount), "plan"
        mine = my_bids.get(lot_id)
        if mine is not None:
            row.my_bid = _bid_amount(mine)
            row.my_bid_status = mine.status
        # Цена победителя — из его заявки, иначе из договора: по проигранному
        # лоту это единственный способ увидеть, на сколько разошлись.
        winning = next(
            (b for b in lot_bids.get(lot_id, []) if b.status == WINNER_STATUS), None
        )
        if winning is not None:
            row.winner_amount = _bid_amount(winning)
            row.winner_name = row.winner_name or winning.supplier_name
            row.winner_bin = row.winner_bin or winning.supplier_bin
        elif c_amount is not None:
            row.winner_amount = _f(c_amount)
        return row

    card.wins = [_row(i) for i in win_ids_shown]
    card.losses = [_row(i) for i in loss_ids_shown]
    card.wins_shown = len(card.wins)
    card.losses_shown = len(card.losses)

    card.name = (
        next((r.winner_name for r in card.wins if r.winner_name), "")
        or next((b.supplier_name for b in my_bids.values() if b.supplier_name), "")
        or (card.org.name if card.org else "")
        or bin_
    )
    return card
