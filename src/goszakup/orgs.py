"""Роль организации (заказчик / организатор / поставщик) — производная, не колонка.

`Organization` одна на все роли намеренно: в реестре участников OWS
`subject` — тоже одна таблица с флагами `customer`/`organizer`/`supplier`,
и они пересекаются (у ГУ «Минфин РК» в примере документации стоят все три).
Расщепление на две таблицы продублировало бы БИН, контакты и
`contacts_synced_at` у тех, кто и закупает, и продаёт, а главное — удвоило
бы дедуп безбиновых строк из листинга (правило #4).

Роль считается по фактам участия в НАШИХ данных, а не по флагам реестра:
флаг `supplier` в реестре стоит почти у всех, включая госорганы, — как
признак роли он бесполезен; плюс роль есть и у тех, кого в реестре нет
(часть ИП) или кого мы ещё не опрашивали (контакты синканы у 700 из 17k
организаций).

Поставщик собирается из трёх мест, потому что три пути наполнения БД дают
его по-разному: договор кладёт FK (`Contract.supplier_id`,
`jobs/contracts`), а победитель из HTML-таба и заявка приезжают строкой БИН
(`lots.winner_bin`, `lot_bids.supplier_bin`) — строки в organizations у них
может не быть вовсе, её создаёт `jobs/supplier_contacts.orgs_to_sync`.

Роли не покрывают таблицу целиком: ~4k организаций (2026-08-07) не попадают
ни в одну — это осиротевшие безбиновые строки из листинга, у которых
`_apply_details` переставил `lot.customer_id` на строку с БИН (правило #4).
Лотов у них нет, поэтому в витрине им и не место.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import aliased

from .db.models import Announcement, Contract, Lot, LotBid, Organization

# Алиасы + correlate(Organization): вызывающие запросы (например
# /organizations) уже джойнят lots, и без этого SQLAlchemy сколлапсировала бы
# подзапрос на внешнюю таблицу — EXISTS стал бы тождественно верным.


def customer_condition():
    lot = aliased(Lot)
    return exists(
        select(1).where(lot.customer_id == Organization.id).correlate(Organization)
    )


def organizer_condition():
    anno = aliased(Announcement)
    return exists(
        select(1).where(anno.organizer_id == Organization.id).correlate(Organization)
    )


def buyer_condition():
    """Организация закупает: заказчик по лоту или организатор объявления."""
    return or_(customer_condition(), organizer_condition())


def supplier_condition():
    lot = aliased(Lot)
    bid = aliased(LotBid)
    contract = aliased(Contract)
    by_bin = and_(
        Organization.bin.is_not(None),
        or_(
            exists(
                select(1)
                .where(lot.winner_bin == Organization.bin)
                .correlate(Organization)
            ),
            exists(
                select(1)
                .where(bid.supplier_bin == Organization.bin)
                .correlate(Organization)
            ),
        ),
    )
    by_fk = exists(
        select(1)
        .where(contract.supplier_id == Organization.id)
        .correlate(Organization)
    )
    return or_(by_bin, by_fk)


ROLE_CONDITIONS = {
    "customer": customer_condition,
    "organizer": organizer_condition,
    "supplier": supplier_condition,
}


def role_condition(role: str | None):
    """Условие по коду роли из query-параметра; неизвестное — «все закупающие»."""
    factory = ROLE_CONDITIONS.get(role or "")
    return factory() if factory is not None else buyer_condition()
