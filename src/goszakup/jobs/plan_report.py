"""Выборки и агрегаты по годовому плану — витрина /plans и врезки в отчёты.

Чистый SQL по `plan_points` (заливает jobs/plans.py), к goszakup не ходит.
Смысл витрины — «что заказчики только собираются купить»: по умолчанию
показываем пункты, по которым объявления ещё нет, в вертикалях и регионах
scope пользователя.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, desc, func, or_, select
from sqlalchemy.orm import Session

from ..db.models import PlanPoint
from .plans import MONTH_PAST_YEAR, PLANNED_STATUSES

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь",
    7: "Июль", 8: "Август", 9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь",
    12: "Декабрь", MONTH_PAST_YEAR: "Прошлый год",
}

STAGES = (
    ("planned", "Объявления ещё нет"),
    ("announced", "Уже объявлено"),
    ("all", "Все стадии"),
)

# Электронный магазин — половина плана и не тендерный рынок (закупка в
# один клик из каталога), поэтому в витрине по умолчанию скрыт.
EMARKET_METHOD_ID = 60

SORTS = {
    "-amount": desc(PlanPoint.amount),
    "amount": PlanPoint.amount,
    "month": PlanPoint.month,
    "-created_at": desc(PlanPoint.created_at),
}


def month_label(month: int | None) -> str:
    return MONTH_NAMES.get(month or 0, "—")


@dataclass
class PlanFilters:
    q: str = ""
    kato: str = ""
    category: str = ""
    month: int | None = None
    stage: str = "planned"
    method: int | None = None
    with_emarket: bool = False
    amount_from: int | None = None
    amount_to: int | None = None
    year: int | None = None
    sort: str = "-amount"


def plan_conditions(f: PlanFilters) -> list:
    conds = [PlanPoint.is_active.is_(True)]
    if f.year:
        conds.append(PlanPoint.year == f.year)
    if f.stage == "planned":
        conds.append(PlanPoint.status_id.in_(PLANNED_STATUSES))
    elif f.stage == "announced":
        conds.append(PlanPoint.status_id.not_in(PLANNED_STATUSES))
    if f.kato:
        conds.append(PlanPoint.kato == f.kato)
    if f.category == "other":
        conds.append(PlanPoint.category.is_(None))
    elif f.category:
        conds.append(PlanPoint.category == f.category)
    if f.month:
        conds.append(PlanPoint.month == f.month)
    if f.method:
        conds.append(PlanPoint.trade_method_id == f.method)
    elif not f.with_emarket:
        conds.append(PlanPoint.trade_method_id != EMARKET_METHOD_ID)
    if f.amount_from is not None:
        conds.append(PlanPoint.amount >= f.amount_from)
    if f.amount_to is not None:
        conds.append(PlanPoint.amount <= f.amount_to)
    if f.q:
        like = f"%{f.q.strip()}%"
        conds.append(
            or_(
                PlanPoint.name.ilike(like),
                PlanPoint.description.ilike(like),
                PlanPoint.customer_name.ilike(like),
                PlanPoint.enstru_name.ilike(like),
                PlanPoint.enstru_code.like(f"{f.q.strip()}%"),
            )
        )
    return conds


def plan_query(f: PlanFilters, scope_conds: list) -> Select:
    return select(PlanPoint).where(*plan_conditions(f), *scope_conds)


def apply_sort(stmt: Select, sort: str) -> Select:
    return stmt.order_by(SORTS.get(sort, SORTS["-amount"]))


def plan_totals(session: Session, f: PlanFilters, scope_conds: list) -> tuple[int, float]:
    row = session.execute(
        select(func.count(PlanPoint.root_id), func.coalesce(func.sum(PlanPoint.amount), 0))
        .where(*plan_conditions(f), *scope_conds)
    ).one()
    return int(row[0] or 0), float(row[1] or 0)


def by_month(session: Session, f: PlanFilters, scope_conds: list) -> list[dict]:
    """Разбивка «сколько и на сколько запланировано по месяцам»."""
    rows = session.execute(
        select(
            PlanPoint.month,
            func.count(PlanPoint.root_id),
            func.coalesce(func.sum(PlanPoint.amount), 0),
        )
        .where(*plan_conditions(f), *scope_conds)
        .group_by(PlanPoint.month)
        .order_by(PlanPoint.month)
    ).all()
    return [
        {"month": m, "label": month_label(m), "n": int(n), "total": float(total or 0)}
        for m, n, total in rows
    ]


_METHODS_TTL_SEC = 600.0
_methods_cache: dict[str, object] = {}


def trade_methods(session: Session) -> list[tuple[int, str]]:
    """Способы закупки, реально встречающиеся в плане.

    GROUP BY по 2.5 млн строк — секунды, поэтому результат кешируется в
    процессе: список меняется раз в год, а не от прогона к прогону.
    """
    now = time.monotonic()
    cached = _methods_cache.get("data")
    if cached is not None and (now - float(_methods_cache["at"])) < _METHODS_TTL_SEC:
        return cached  # type: ignore[return-value]
    rows = session.execute(
        select(PlanPoint.trade_method_id, func.max(PlanPoint.trade_method))
        .where(PlanPoint.trade_method_id.is_not(None))
        .group_by(PlanPoint.trade_method_id)
        .order_by(func.count(PlanPoint.root_id).desc())
        .limit(20)
    ).all()
    data = [(int(mid), name or str(mid)) for mid, name in rows]
    _methods_cache.update({"at": now, "data": data})
    return data


def plan_years(session: Session) -> list[int]:
    return [
        int(y)
        for y in session.scalars(
            select(PlanPoint.year)
            .where(PlanPoint.year.is_not(None))
            .distinct()
            .order_by(PlanPoint.year.desc())
        )
    ]


def upcoming_summary(
    session: Session, scope_conds: list, *, now: datetime | None = None
) -> dict:
    """Карточка дашборда «ожидается по плану»: незаобъявленные пункты
    текущего и следующего месяца в scope пользователя."""
    now = now or datetime.now(UTC)
    months = [now.month] if now.month == 12 else [now.month, now.month + 1]
    f = PlanFilters(year=now.year, stage="planned")
    row = session.execute(
        select(
            func.count(PlanPoint.root_id),
            func.coalesce(func.sum(PlanPoint.amount), 0),
        ).where(
            *plan_conditions(f), *scope_conds, PlanPoint.month.in_(months)
        )
    ).one()
    return {
        "n": int(row[0] or 0),
        "total": float(row[1] or 0),
        "months": [month_label(m) for m in months],
    }


def org_plan_summary(session: Session, bins: list[str], *, year: int) -> dict:
    """Годовой план организации: сколько запланировано, объявлено, осталось.

    Организация ищется по БИН — план хранится плоско, без FK (см. модель
    PlanPoint): у большинства заказчиков плана лоты в БД есть далеко не все.
    """
    if not bins:
        return {"total_n": 0, "total_sum": 0.0, "planned_n": 0, "planned_sum": 0.0,
                "announced_n": 0, "announced_sum": 0.0, "by_month": [], "top": []}
    base = [
        PlanPoint.customer_bin.in_(bins),
        PlanPoint.year == year,
        PlanPoint.is_active.is_(True),
    ]
    planned_cond = PlanPoint.status_id.in_(PLANNED_STATUSES)
    row = session.execute(
        select(
            func.count(PlanPoint.root_id),
            func.coalesce(func.sum(PlanPoint.amount), 0),
            func.count(PlanPoint.root_id).filter(planned_cond),
            func.coalesce(func.sum(PlanPoint.amount).filter(planned_cond), 0),
        ).where(*base)
    ).one()
    months = session.execute(
        select(
            PlanPoint.month,
            func.count(PlanPoint.root_id),
            func.coalesce(func.sum(PlanPoint.amount), 0),
        )
        .where(*base)
        .group_by(PlanPoint.month)
        .order_by(PlanPoint.month)
    ).all()
    top = session.scalars(
        select(PlanPoint)
        .where(*base, planned_cond)
        .order_by(desc(PlanPoint.amount))
        .limit(10)
    ).all()
    total_n, total_sum, planned_n, planned_sum = row
    return {
        "total_n": int(total_n or 0),
        "total_sum": float(total_sum or 0),
        "planned_n": int(planned_n or 0),
        "planned_sum": float(planned_sum or 0),
        "announced_n": int(total_n or 0) - int(planned_n or 0),
        "announced_sum": float(total_sum or 0) - float(planned_sum or 0),
        "by_month": [
            {"month": m, "label": month_label(m), "n": int(n), "total": float(t or 0)}
            for m, n, t in months
        ],
        "top": list(top),
    }


def render_csv(rows: list[PlanPoint]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        [
            "БИН заказчика",
            "Заказчик",
            "Предмет",
            "Характеристика",
            "Код ЕНС ТРУ",
            "Сумма, тг",
            "Месяц",
            "Способ закупки",
            "Статус пункта",
            "Аванс, %",
            "Срок поставки",
            "Место поставки",
            "Финансирование",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r.customer_bin or "",
                r.customer_name or "",
                r.name or "",
                r.description or "",
                r.enstru_code or "",
                round(float(r.amount or 0)),
                month_label(r.month),
                r.trade_method or "",
                r.status_name or "",
                r.prepayment or 0,
                r.supply_date or "",
                r.place or "",
                r.finsource or "",
            ]
        )
    return buf.getvalue()
