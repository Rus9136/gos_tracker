"""Синк годового плана закупок из GraphQL Plans (OWS).

План — единственный источник, где закупка видна ДО объявления: у открытых
конкурсов пункт появляется за 1–8 недель, у ЗЦП часто в тот же день. Оттуда
же приходят поля, которых в лоте нет вовсе: плановый месяц, аванс, срок
поставки, источник финансирования и бюджетные статьи.

Два неочевидных свойства источника (замерено вживую 2026-08-06):

1. **Фильтры по датам не работают.** `dateCreate`/`timestamp`/`indexDate`
   при непустом «от» отдают 0 записей, а «до» игнорируется. Инкремент по
   окну, как у contracts-sync, здесь невозможен. Зато выдача идёт по id
   DESC и курсор `after` работает — поэтому окно задаётся id: идём от
   свежих вниз, пока не упрёмся в `max(point_id)` своей БД.
2. **Правка пункта создаёт новую строку** с новым id и прежним
   `rootrecordId`. Поэтому ключ хранения — root_id, а инкремент по id
   ловит и правки: у изменённого пункта id всегда свежее водяного знака.

Полный проход за год — 2.6 млн пунктов, ~13 тыс. страниц по 200 (limit
жёстко ограничен сервером) ≈ 7 часов при 1 rps. Это разовый бэкофилл
(`sync_plans(year=...)`, возобновляемый), ежедневный инкремент — десяток
страниц.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..api.client import OwsClient
from ..api.mapping import almaty_to_utc, region_from_kato_list
from ..api.queries import PLANS_QUERY
from ..classify.verticals import classify_vertical
from ..db.models import Lot, PlanPoint

log = logging.getLogger(__name__)

# Пункт внесён в план, но объявления по нему ещё нет (444 — камеральный
# контроль перед публикацией). Остальные статусы (5 «Опубликован»,
# 9 «Закупка состоялась», 310 «Проект договора», 20 «Отказ от закупки», …)
# означают, что закупка уже началась, закончилась или отменена.
PLANNED_STATUSES = (2, 3, 6, 25, 26, 28, 444)

# refMonthsId: 1–12 — месяцы, 99 — «Прошлый год» (переходящие закупки).
MONTH_PAST_YEAR = 99

# Столько страниц берём за один инкрементальный прогон. Суточный поток
# правок по всему РК — ~2.4 тыс. записей (12 страниц), потолок нужен на
# случай, если водяной знак потерялся: лучше отстать, чем повесить актор
# на семичасовой проход.
INCREMENTAL_MAX_PAGES = 200

_NUMBER_RE = re.compile(r"^(\d+)-")


@dataclass
class PlansSyncStats:
    scanned: int = 0
    created: int = 0
    updated: int = 0
    linked: int = 0
    last_id: int | None = None


def plan_root_from_number(number: str | None) -> int | None:
    """Номер лота на goszakup — «<rootrecordId пункта плана>-<способ><N>»
    (проверено на всех номерах в БД). Связь лота с планом считается отсюда,
    без запроса к API: обратного фильтра `Lots.plnPointRootrecordId` в OWS
    фактически нет — он отдаёт пусто даже для существующих пунктов."""
    if not number:
        return None
    m = _NUMBER_RE.match(number)
    return int(m.group(1)) if m else None


def plan_watermark(session: Session) -> int | None:
    """Водяной знак инкремента — самый свежий id, который мы уже видели."""
    return session.scalar(select(func.max(PlanPoint.point_id)))


def _money(v) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


def _spec_rows(p: dict) -> list[dict] | None:
    rows = [
        {
            "ekrb_code": s.get("ekrbCode") or "",
            "ekrb_name": s.get("ekrbNameRu") or "",
            "program_code": s.get("fkrbProgramCode") or "",
            "program_name": s.get("fkrbProgramNameRu") or "",
            "abp_code": s.get("abpCode") or "",
            "abp_name": s.get("abpNameRu") or "",
            "amount": float(s["amount"]) if s.get("amount") is not None else None,
        }
        for s in p.get("PlansSpec") or []
    ]
    return rows or None


def _fields(p: dict) -> dict:
    katos = [k.get("refKatoCode") or "" for k in p.get("PlansKato") or []]
    places = [
        k.get("fullDeliveryPlaceNameRu") or "" for k in p.get("PlansKato") or []
    ]
    enstru_code = p.get("refEnstruCode") or (p.get("RefEnstru") or {}).get("code") or ""
    enstru_name = (p.get("RefEnstru") or {}).get("nameRu") or ""
    acts = p.get("PlanActs") or {}
    return {
        "point_id": int(p["id"]),
        "year": p.get("plnPointYear"),
        "customer_bin": p.get("subjectBiin") or None,
        "customer_name": p.get("subjectNameRu") or None,
        "name": p.get("nameRu") or None,
        "description": p.get("descRu") or None,
        "extra_description": p.get("extraDescRu") or None,
        "enstru_code": enstru_code or None,
        "enstru_name": enstru_name or None,
        # Тот же классификатор, что у лотов (правило #24): вертикаль пункта
        # плана и вертикаль объявленного из него лота обязаны совпадать,
        # иначе scope пользователя показывал бы разное на /plans и /actual.
        "category": classify_vertical(enstru_code, enstru_name, p.get("nameRu")),
        "amount": _money(p.get("amount")),
        "price": _money(p.get("price")),
        "quantity": float(p["count"]) if p.get("count") is not None else None,
        "unit": (p.get("RefUnits") or {}).get("nameRu") or None,
        "month": p.get("refMonthsId"),
        "trade_method_id": p.get("refTradeMethodsId"),
        "trade_method": (p.get("RefTradeMethods") or {}).get("nameRu") or None,
        "status_id": p.get("refPlnPointStatusId"),
        "status_name": (p.get("RefPlnPointStatus") or {}).get("nameRu") or None,
        "subject_type": (p.get("RefSubjectType") or {}).get("nameRu") or None,
        "prepayment": float(p["prepayment"]) if p.get("prepayment") is not None else None,
        "supply_date": p.get("supplyDateRu") or None,
        "finsource": (p.get("RefFinsource") or {}).get("nameRu") or None,
        "budget_type": (p.get("RefBudgetType") or {}).get("nameRu") or None,
        "spec": _spec_rows(p),
        "kato": region_from_kato_list(katos) or None,
        "place": next((x for x in places if x), None),
        "plan_act_number": acts.get("planActNumber") or None,
        "plan_act_approved_at": almaty_to_utc(acts.get("dateApproved")),
        "is_active": bool(p.get("isActive", 1)),
        "created_at": almaty_to_utc(p.get("dateCreate")),
        "changed_at": almaty_to_utc(p.get("timestamp")),
    }


def upsert_plan_point(session: Session, p: dict) -> str | None:
    """'created' / 'updated' / None (пришла версия не новее сохранённой)."""
    root_id = int(p.get("rootrecordId") or p["id"])
    fields = _fields(p)
    row = session.get(PlanPoint, root_id)
    if row is None:
        row = PlanPoint(
            root_id=root_id,
            amount_initial=fields["amount"],
            month_initial=fields["month"],
            **fields,
        )
        session.add(row)
        return "created"
    # Бэкофилл идёт по id DESC, поэтому после свежей версии может приехать
    # старая — она не должна затирать актуальные сумму и статус.
    if row.point_id >= fields["point_id"]:
        return None
    row.versions = (row.versions or 1) + 1
    for k, v in fields.items():
        setattr(row, k, v)
    return "updated"


def link_lots(session: Session, *, batch: int = 5000) -> int:
    """Проставить `Lot.plan_root_id` лотам, у которых он ещё пуст.

    Разбор номера — в Python, а не в SQL: `split_part` есть только в
    Postgres, а на dev-SQLite такой UPDATE молча не сработал бы.
    """
    linked = 0
    after = 0
    while True:
        rows = session.execute(
            select(Lot.id, Lot.number)
            .where(
                Lot.plan_root_id.is_(None),
                Lot.number.is_not(None),
                Lot.id > after,
            )
            .order_by(Lot.id)
            .limit(batch)
        ).all()
        if not rows:
            return linked
        # Курсор по id, а не «выбирать снова всё с plan_root_id IS NULL»:
        # у лота с неразбираемым номером поле так и останется пустым, и
        # выборка по одному лишь условию зациклилась бы на нём.
        after = rows[-1][0]
        mapping = [
            {"id": lot_id, "plan_root_id": root}
            for lot_id, number in rows
            if (root := plan_root_from_number(number)) is not None
        ]
        if mapping:
            session.execute(update(Lot), mapping)
            session.commit()
            linked += len(mapping)


def sync_plans(
    session: Session,
    client: OwsClient,
    *,
    year: int | None = None,
    stop_at_id: int | None = None,
    start_after: int | None = None,
    max_pages: int | None = None,
    on_progress=None,
) -> PlansSyncStats:
    """Обход Plans по id DESC.

    `stop_at_id` — водяной знак инкремента: дойдя до пункта не свежее его,
    останавливаемся. `year` сужает бэкофилл до финансового года; в
    инкременте фильтр не ставим — правки пунктов прошлых лет тоже наши.
    """
    stats = PlansSyncStats()
    variables = {"f": {"plnPointYear": [year]} if year else {}}
    batch_seen = 0
    for p in client.iter_graphql(
        PLANS_QUERY,
        variables,
        root="Plans",
        limit=200,
        max_pages=max_pages,
        start_after=start_after,
    ):
        point_id = int(p["id"])
        if stop_at_id is not None and point_id <= stop_at_id:
            break
        stats.scanned += 1
        stats.last_id = point_id
        res = upsert_plan_point(session, p)
        if res == "created":
            stats.created += 1
        elif res == "updated":
            stats.updated += 1
        batch_seen += 1
        if batch_seen >= 200:
            _commit(session)
            batch_seen = 0
            if on_progress is not None:
                on_progress(stats)
    _commit(session)
    if on_progress is not None:
        on_progress(stats)
    return stats


def _commit(session: Session) -> None:
    """Гонка двух писателей плана — не ошибка, а норма.

    Многочасовой бэкофилл идёт параллельно с инкрементом из daily, и один и
    тот же новый пункт они могут вставить одновременно (PK — root_id из
    API, а не sequence). Пачка откатывается целиком; её доберёт следующий
    прогон — водяной знак от неудачного коммита не сдвигается.
    """
    from sqlalchemy.exc import IntegrityError

    try:
        session.commit()
    except IntegrityError as e:
        session.rollback()
        log.warning("plans-sync: пачка откачена (параллельный писатель): %s", e)


def backfill_after(session: Session, year: int) -> int | None:
    """Курсор для возобновления бэкофилла: обход идёт по id вниз, значит
    продолжать надо с самого старого сохранённого пункта этого года."""
    return session.scalar(
        select(func.min(PlanPoint.point_id)).where(PlanPoint.year == year)
    )


def current_plan_year(now: datetime | None = None) -> int:
    return (now or datetime.now(UTC)).year
