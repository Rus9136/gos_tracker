"""Отчёт по закупкам организации: SQL-агрегаты по уже загруженным лотам.

К goszakup НЕ ходит и LLM не зовёт — считает только то, что уже в БД.
Чтобы отчёт был полным, лоты организации сначала догружаются через /ingest
(по БИН, с деталями и победителями), затем открывается отчёт.
"""

from __future__ import annotations

from sqlalchemy import Select, desc, extract, func, or_, select
from sqlalchemy.orm import Session

from ..classify.verticals import VERTICAL_LABELS
from ..db.models import Announcement, Contract, Lot, Organization

TOP_LIMIT = 20

# «Закупка состоялась» — только такие лоты дают победителя и договор.
_OK_STATUS = 360


def related_org_ids(session: Session, org: Organization) -> list[int]:
    """Одна реальная организация часто живёт в БД несколькими строками:
    customer из листинга — без БИН (goszakup его там не отдаёт, правило #4),
    organizer из деталей — с БИН. Склеиваем по БИН и точному имени."""
    conds = [Organization.id == org.id]
    if org.bin:
        conds.append(Organization.bin == org.bin)
    if org.name:
        conds.append(Organization.name == org.name)
    return list(session.scalars(select(Organization.id).where(or_(*conds))))


def _contract_amount_sq():
    return (
        select(func.max(Contract.contract_amount))
        .where(Contract.lot_id == Lot.id)
        .scalar_subquery()
    )


def _org_lots(org_ids: list[int]) -> Select:
    """Базовый FROM: лоты, где организация — заказчик лота или организатор
    объявления. outerjoin — у stub-лотов объявление может быть не заполнено."""
    return (
        select(Lot)
        .outerjoin(Announcement, Announcement.id == Lot.announcement_id)
        .where(
            or_(
                Lot.customer_id.in_(org_ids),
                Announcement.organizer_id.in_(org_ids),
            )
        )
    )


def build_org_report(session: Session, org: Organization) -> dict:
    org_ids = related_org_ids(session, org)
    base = _org_lots(org_ids)
    contract_sq = _contract_amount_sq()
    lot_ids_sq = base.with_only_columns(Lot.id).scalar_subquery()

    kpis = session.execute(
        select(
            func.count(Lot.id).label("n_lots"),
            func.count(func.distinct(Lot.announcement_id)).label("n_annos"),
            func.count(Lot.id).filter(Lot.status_code == _OK_STATUS).label("n_ok"),
            func.count(Lot.id).filter(Lot.winner_bin.is_not(None)).label("n_winner"),
            func.coalesce(
                func.sum(contract_sq).filter(Lot.status_code == _OK_STATUS), 0
            ).label("contract_total"),
            func.count(Lot.id).filter(Lot.category.is_not(None)).label("cat_n"),
            func.count(Lot.id)
            .filter(Lot.category.is_not(None), Lot.status_code == _OK_STATUS)
            .label("cat_ok"),
            func.coalesce(
                func.sum(contract_sq).filter(
                    Lot.category.is_not(None), Lot.status_code == _OK_STATUS
                ),
                0,
            ).label("cat_contract_total"),
        ).where(Lot.id.in_(lot_ids_sq))
    ).one()

    yr = extract("year", Announcement.publish_date)
    years = session.execute(
        select(
            yr.label("yr"),
            func.count(Lot.id).label("n_all"),
            func.count(Lot.id).filter(Lot.status_code == _OK_STATUS).label("n_ok"),
            func.coalesce(
                func.sum(Lot.plan_amount).filter(Lot.status_code == _OK_STATUS), 0
            ).label("plan_ok"),
            func.coalesce(
                func.sum(contract_sq).filter(Lot.status_code == _OK_STATUS), 0
            ).label("contract_ok"),
        )
        .select_from(Lot)
        .join(Announcement, Announcement.id == Lot.announcement_id)
        .where(Lot.id.in_(lot_ids_sq), Announcement.publish_date.is_not(None))
        .group_by(yr)
        .order_by(yr)
    ).all()

    contract_ok = func.coalesce(
        func.sum(contract_sq).filter(Lot.status_code == _OK_STATUS), 0
    )
    cats = session.execute(
        select(
            Lot.enstru,
            func.count(Lot.id).label("n_all"),
            func.count(Lot.id).filter(Lot.status_code == _OK_STATUS).label("n_ok"),
            func.coalesce(
                func.sum(Lot.plan_amount).filter(Lot.status_code == _OK_STATUS), 0
            ).label("plan_ok"),
            contract_ok.label("contract_ok"),
        )
        .where(Lot.id.in_(lot_ids_sq))
        .group_by(Lot.enstru)
        .order_by(desc("contract_ok"), desc("n_all"))
        .limit(TOP_LIMIT)
    ).all()

    won_total = func.coalesce(
        func.sum(func.coalesce(contract_sq, Lot.plan_amount)), 0
    )
    winners = session.execute(
        select(
            Lot.winner_bin,
            func.max(Lot.winner_name).label("winner_name"),
            func.count(Lot.id).label("n"),
            won_total.label("total"),
        )
        .where(Lot.id.in_(lot_ids_sq), Lot.winner_bin.is_not(None))
        .group_by(Lot.winner_bin)
        .order_by(desc("total"))
        .limit(TOP_LIMIT)
    ).all()

    cat_lots = session.execute(
        select(
            Lot.id,
            extract("year", Announcement.publish_date).label("yr"),
            Lot.name,
            Lot.category,
            Lot.plan_amount,
            contract_sq.label("contract_amount"),
            Lot.winner_bin,
            Lot.winner_name,
        )
        .outerjoin(Announcement, Announcement.id == Lot.announcement_id)
        .where(
            Lot.id.in_(lot_ids_sq),
            Lot.category.is_not(None),
            Lot.status_code == _OK_STATUS,
        )
        .order_by(Announcement.publish_date)
    ).all()

    actual_lots = session.execute(
        select(
            Lot.id,
            Lot.name,
            Lot.status_name,
            Lot.category,
            Lot.plan_amount,
            Announcement.application_end,
        )
        .outerjoin(Announcement, Announcement.id == Lot.announcement_id)
        .where(Lot.id.in_(lot_ids_sq), Lot.is_actual.is_(True))
        .order_by(desc(Lot.plan_amount))
    ).all()

    return {
        "org": org,
        "org_ids": org_ids,
        "kpis": kpis,
        "years": years,
        "cats": cats,
        "winners": winners,
        "cat_lots": cat_lots,
        "actual_lots": actual_lots,
    }


def _t(n) -> str:
    if n is None:
        return "—"
    return f"{round(n):,}".replace(",", " ")


def _v(slug) -> str:
    return VERTICAL_LABELS.get(slug, slug or "—")


def render_markdown(report: dict, base_url: str = "") -> str:
    org = report["org"]
    k = report["kpis"]
    L: list[str] = []
    L.append(f"# Отчёт по закупкам: {org.name}")
    L.append("")
    if org.bin:
        L.append(f"**БИН:** {org.bin}")
    share = round(k.n_ok / k.n_lots * 100) if k.n_lots else 0
    L.append("")
    L.append("## Ключевые цифры")
    L.append("")
    L.append("| Показатель | Значение |")
    L.append("|---|---|")
    L.append(f"| Всего лотов в БД | {k.n_lots} (в {k.n_annos} объявлениях) |")
    L.append(f"| Состоялись | {k.n_ok} ({share}%), у {k.n_winner} записан победитель |")
    L.append(f"| Сумма договоров (без НДС) | {_t(k.contract_total)} ₸ |")
    L.append(
        f"| Лоты в вертикалях | {k.cat_n}, из них {k.cat_ok} состоялись"
        f" на {_t(k.cat_contract_total)} ₸ |"
    )
    if report["actual_lots"]:
        L.append("")
        L.append("## Открытые лоты сейчас")
        L.append("")
        L.append("| Лот | Статус | Вертикаль | План, ₸ | Приём до (UTC) |")
        L.append("|---|---|---|---:|---|")
        for a in report["actual_lots"]:
            name = (a.name or "—").replace("|", "/")[:80]
            L.append(
                f"| [{name}]({base_url}/lot/{a.id}) | {a.status_name or '—'} "
                f"| {_v(a.category)} | {_t(a.plan_amount)} "
                f"| {a.application_end or '—'} |"
            )
    L.append("")
    L.append("## Динамика по годам (состоявшиеся)")
    L.append("")
    L.append("| Год | Лотов всего | Состоялось | План, ₸ | Договоры, ₸ |")
    L.append("|---|---:|---:|---:|---:|")
    for y in report["years"]:
        L.append(
            f"| {int(y.yr)} | {y.n_all} | {y.n_ok} "
            f"| {_t(y.plan_ok)} | {_t(y.contract_ok)} |"
        )
    if report["cat_lots"]:
        L.append("")
        L.append(f"## Состоявшиеся лоты в вертикалях ({len(report['cat_lots'])})")
        L.append("")
        L.append("| Год | Лот | Вертикаль | План, ₸ | Договор, ₸ | Победитель |")
        L.append("|---|---|---|---:|---:|---|")
        for lt in report["cat_lots"]:
            name = (lt.name or "—").replace("|", "/")[:80]
            win = (lt.winner_name or "—").replace("|", "/")
            if lt.winner_bin:
                win += f" ({lt.winner_bin})"
            contract = _t(lt.contract_amount) if lt.contract_amount is not None else "—"
            L.append(
                f"| {int(lt.yr) if lt.yr else '—'} "
                f"| [{name}]({base_url}/lot/{lt.id}) | {_v(lt.category)} "
                f"| {_t(lt.plan_amount)} | {contract} | {win} |"
            )
    L.append("")
    L.append(f"## Категории — топ-{TOP_LIMIT} по сумме договоров")
    L.append("")
    L.append("| Категория (ЕНС ТРУ) | Лотов | Состоялось | План, ₸ | Договоры, ₸ |")
    L.append("|---|---:|---:|---:|---:|")
    for c in report["cats"]:
        cshare = round(c.n_ok / c.n_all * 100) if c.n_all else 0
        L.append(
            f"| {(c.enstru or '—').replace('|', '/')} | {c.n_all} "
            f"| {c.n_ok} ({cshare}%) | {_t(c.plan_ok)} | {_t(c.contract_ok)} |"
        )
    L.append("")
    L.append(f"## Поставщики-победители — топ-{TOP_LIMIT}")
    L.append("")
    L.append("| Поставщик | БИН/ИИН | Лотов | Сумма, ₸ |")
    L.append("|---|---|---:|---:|")
    for w in report["winners"]:
        L.append(
            f"| {(w.winner_name or '—').replace('|', '/')} | {w.winner_bin} "
            f"| {w.n} | {_t(w.total)} |"
        )
    L.append("")
    L.append(
        "_Отчёт построен по данным, загруженным в трекер с goszakup.gov.kz. "
        "«Не состоялась» чаще всего означает отсутствие участников — закупка "
        "публикуется повторно. Суммы договоров — без НДС; где договора нет, "
        "в топе победителей учтена плановая сумма._"
    )
    L.append("")
    return "\n".join(L)
