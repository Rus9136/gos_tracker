"""Сборка текста Telegram-уведомления о подходящем лоте.

Parse-mode HTML (см. notify/telegram.py) — все подставляемые значения
экранируем через html.escape, иначе `<`/`&` в названии лота сломают разметку.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape

from ..config import PUBLIC_BASE_URL
from ..db.models import Lot, UserLotMatch, UserQuery
from ..scraper.katos import region_name


def _fmt_amount(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    try:
        return f"{value:,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def build_match_message(query: UserQuery, lot: Lot, match: UserLotMatch) -> str:
    name = (lot.name or "Лот без названия").strip()
    region = region_name(lot.kato) or lot.kato or "—"
    amount = _fmt_amount(lot.plan_amount)
    site_url = f"{PUBLIC_BASE_URL}/lot/{lot.id}"

    lines = [
        f"🔔 <b>Новый лот по запросу «{escape(query.name)}»</b>",
        "",
        f"<b>{escape(name)}</b>",
        f"💰 {escape(amount)} ₸   📍 {escape(region)}",
        f"🎯 Релевантность: {match.score}/100",
    ]
    if match.reason:
        lines.append(f"💬 {escape(match.reason.strip())}")
    lines += [
        "",
        f'<a href="{escape(site_url)}">Открыть в трекере</a> · '
        f'<a href="{escape(lot.url)}">goszakup</a>',
    ]
    return "\n".join(lines)


def build_plan_message(query: UserQuery, point) -> str:
    """Уведомление о новом пункте годового плана (правило #26).

    Ссылки на лот тут нет и быть не может: объявления ещё не существует —
    ведём в витрину плана, отфильтрованную по заказчику.
    """
    from ..jobs.plan_report import month_label

    name = (point.name or "Без названия").strip()
    region = region_name(point.kato) or point.kato or "—"
    plans_url = f"{PUBLIC_BASE_URL}/plans?q={point.customer_bin or ''}&stage=all"

    lines = [
        f"📋 <b>В план добавлена закупка по запросу «{escape(query.name)}»</b>",
        "",
        f"<b>{escape(name)}</b>",
    ]
    if point.description:
        lines.append(escape(point.description.strip()))
    lines += [
        f"💰 {escape(_fmt_amount(point.amount))} ₸   📍 {escape(region)}",
        f"📅 Ожидается: {escape(month_label(point.month))}"
        + (f"   💳 аванс {int(point.prepayment)}%" if point.prepayment else ""),
        f"🏛 {escape((point.customer_name or '—')[:120])}",
        "",
        "Объявления ещё нет — это намерение заказчика.",
        f'<a href="{escape(plans_url)}">План этого заказчика</a>',
    ]
    return "\n".join(lines)


def build_explain_keyboard(lot: Lot) -> dict:
    """Inline-кнопка «Подробнее»: callback уходит на наш вебхук
    (web POST /telegram/webhook), ответ готовит explain_actor."""
    return {
        "inline_keyboard": [
            [{"text": "🤖 Подробнее о лоте", "callback_data": f"explain:{lot.id}"}]
        ]
    }


def build_explain_message(lot: Lot, explanation: str) -> str:
    name = (lot.name or "Лот без названия").strip()
    site_url = f"{PUBLIC_BASE_URL}/lot/{lot.id}"
    return "\n".join(
        [
            f"🤖 <b>Простыми словами: {escape(name)}</b>",
            "",
            escape(explanation.strip()),
            "",
            f'<a href="{escape(site_url)}">Открыть в трекере</a> · '
            f'<a href="{escape(lot.url)}">goszakup</a>',
        ]
    )
