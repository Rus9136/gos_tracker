"""Синк заявок поставщиков с ценами (GraphQL TrdApp → AppLots).

Даёт то, чего не было ни в HTML-пути, ни в синке договоров: цены всех
участников по лоту, их статусы («Победитель», «Второй победитель»,
«Отклонено») и число конкурентов. Победитель тут появляется раньше, чем
договор, — на выборке из 40 наших IT-лотов у 12 победитель был в API,
когда в БД `lot.winner_bin` ещё пустовал (правило #5: закрытые лоты не
пересканируются).

ПОЧЕМУ НЕ ОКНО ПО `dateApply`, как у contracts-sync. Заявки sealed-bid:
пока приём открыт, OWS по лоту не отдаёт ничего. Заявка, поданная 20-го по
лоту с дедлайном 30-го, станет видна только 30-го — а её `dateApply` к тому
моменту давно позади окна, которое мы уже прошли. Такой проход терял бы
ровно те лоты, где конкуренция была. Поэтому идём ОТ СВОИХ лотов: берём те,
у кого дедлайн уже прошёл, и опрашиваем объявление поимённо.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.client import OwsApiError, OwsClient
from ..api.mapping import almaty_to_utc
from ..api.queries import BIDS_QUERY
from ..db.models import Announcement, Lot, LotBid
from .run_preset import _get_or_create_org

log = logging.getLogger(__name__)

WINNER_STATUS = "Победитель"
# Пока победителя нет, объявление опрашивается повторно: статусы вызревают
# («Подано» → «Допущено» → «Победитель») уже после дедлайна. За горизонтом
# лот считаем безнадёжным (не состоялся / отменён) и перестаём дёргать —
# иначе такие объявления копились бы в очереди вечно.
RETRY_HORIZON_DAYS = 45
# Сразу после дедлайна итогов ещё нет: на замере у объявлений моложе суток
# заявки были у 2 из 10, а на возрасте 3-10 дней — у 9 из 10. Не тратим на
# них вызовы, они вернутся в выборку сами.
GRACE_HOURS = 48
# Как часто перепроверять объявление, где победителя ещё нет. Без паузы
# выборка каждый прогон возвращала бы одни и те же свежие объявления, а
# накопленный хвост не разбирался бы никогда.
RECHECK_HOURS = 72


@dataclass
class BidsSyncStats:
    announcements: int = 0
    bids_seen: int = 0
    created: int = 0
    updated: int = 0
    winners_filled: int = 0
    errors: int = 0


def announcements_to_sync(
    session: Session, *, horizon_days: int = RETRY_HORIZON_DAYS, limit: int = 500
) -> list[int]:
    """Объявления с прошедшим дедлайном, по которым победитель ещё не известен.

    Условие «нет строки со статусом Победитель» выключает объявление из выборки
    навсегда, как только итоги подведены. Пара GRACE/RECHECK решает вторую
    задачу — чтобы выборка ПРОДВИГАЛАСЬ: без неё каждый прогон брал бы верхние
    500 по свежести, а накопленный хвост (на момент запуска фичи — 2745
    объявлений старше недели) не разбирался бы никогда.

    Порядок: сперва ни разу не опрошенные, среди них — свежие (там итог только
    что опубликован и он самый ценный), потом давно не проверявшиеся.
    """
    now = datetime.now(UTC)
    won = (
        select(LotBid.lot_id)
        .where(LotBid.lot_id == Lot.id, LotBid.status == WINNER_STATUS)
        .exists()
    )
    q = (
        select(Announcement.id)
        .join(Lot, Lot.announcement_id == Announcement.id)
        .where(
            Announcement.application_end < now - timedelta(hours=GRACE_HOURS),
            Announcement.application_end > now - timedelta(days=horizon_days),
            (Announcement.bids_synced_at.is_(None))
            | (Announcement.bids_synced_at < now - timedelta(hours=RECHECK_HOURS)),
            ~won,
        )
        .group_by(Announcement.id, Announcement.bids_synced_at)
        .order_by(
            Announcement.bids_synced_at.asc().nulls_first(),
            func.max(Announcement.application_end).desc(),
        )
        .limit(limit)
    )
    return list(session.scalars(q))


def _num(v) -> float | None:
    return float(v) if v is not None else None


def upsert_bid(session: Session, lot: Lot, app: dict, app_lot: dict) -> str | None:
    """'created' / 'updated' / None. Ключ — TrdAppLots.id (строка заявка×лот)."""
    bid_id = app_lot.get("id")
    if not bid_id:
        return None
    row = session.get(LotBid, int(bid_id))
    created = row is None
    if created:
        row = LotBid(id=int(bid_id), lot_id=lot.id)
        session.add(row)

    before = (row.status, row.price, row.amount, row.supplier_id)
    supplier = app.get("Supplier") or {}
    bin_ = supplier.get("bin") or app.get("supplierBinIin") or ""
    name = supplier.get("nameRu") or ""

    row.app_id = int(app["id"]) if app.get("id") else row.app_id
    row.supplier_bin = bin_ or row.supplier_bin
    row.supplier_name = name or row.supplier_name
    row.price = _num(app_lot.get("price"))
    row.amount = _num(app_lot.get("amount"))
    row.discount_value = _num(app_lot.get("discountValue"))
    row.discount_price = _num(app_lot.get("discountPrice"))
    row.status = (app_lot.get("RefAppStatus") or {}).get("nameRu") or row.status
    row.date_apply = almaty_to_utc(app.get("dateApply")) or row.date_apply
    # У ИП БИН в API пустой — организацию тогда не заводим (она ключуется по
    # БИН/имени), но имя поставщика уже сохранено строкой выше.
    if bin_:
        org = _get_or_create_org(session, bin_=bin_, name=name)
        if org:
            row.supplier_id = org.id
    session.flush()
    if created:
        return "created"
    after = (row.status, row.price, row.amount, row.supplier_id)
    return "updated" if after != before else None


def apply_winner_from_bid(lot: Lot, row: LotBid) -> bool:
    """Победитель из заявки. Не затираем добытое HTML-ом/договором."""
    if lot.winner_bin or row.status != WINNER_STATUS:
        return False
    if not row.supplier_bin and not row.supplier_name:
        return False
    lot.winner_bin = row.supplier_bin or lot.winner_bin
    lot.winner_name = lot.winner_name or row.supplier_name or ""
    return True


def sync_announcement_bids(
    session: Session, client: OwsClient, anno_id: int, stats: BidsSyncStats
) -> None:
    apps = list(
        client.iter_graphql(
            BIDS_QUERY, {"f": {"buyId": int(anno_id)}}, root="TrdApp", limit=100
        )
    )
    # Отметку ставим и когда заявок нет: именно пустой ответ надо запомнить,
    # иначе объявление вернётся в выборку следующим же прогоном.
    anno = session.get(Announcement, anno_id)
    if anno is not None:
        anno.bids_synced_at = datetime.now(UTC)
    if not apps:
        return
    lot_ids = {
        int(al["lotId"])
        for a in apps
        for al in a.get("AppLots") or []
        if al.get("lotId")
    }
    ours = {
        lot.id: lot for lot in session.scalars(select(Lot).where(Lot.id.in_(lot_ids)))
    }
    for app in apps:
        for app_lot in app.get("AppLots") or []:
            lot = ours.get(int(app_lot.get("lotId") or 0))
            if lot is None:
                continue  # чужой лот того же объявления — не наш профиль
            stats.bids_seen += 1
            res = upsert_bid(session, lot, app, app_lot)
            if res == "created":
                stats.created += 1
            elif res == "updated":
                stats.updated += 1
            row = session.get(LotBid, int(app_lot["id"]))
            if row is not None and apply_winner_from_bid(lot, row):
                stats.winners_filled += 1


def sync_bids(
    session: Session,
    client: OwsClient,
    *,
    horizon_days: int = RETRY_HORIZON_DAYS,
    limit: int = 500,
    on_progress=None,
) -> BidsSyncStats:
    stats = BidsSyncStats()
    anno_ids = announcements_to_sync(
        session, horizon_days=horizon_days, limit=limit
    )
    log.info("bids-sync: объявлений к опросу %d", len(anno_ids))
    for anno_id in anno_ids:
        try:
            sync_announcement_bids(session, client, anno_id, stats)
        except OwsApiError as e:
            # Одно объявление не должно ронять проход: следующий прогон
            # возьмёт его снова (выборка идёт от «нет победителя»).
            log.warning("bids-sync: объявление %s: %s", anno_id, e)
            session.rollback()
            stats.errors += 1
            continue
        stats.announcements += 1
        session.commit()
        if on_progress is not None:
            on_progress(stats)
    return stats
