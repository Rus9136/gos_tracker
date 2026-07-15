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
