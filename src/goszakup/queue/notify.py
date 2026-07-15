"""Dramatiq-слой уведомлений: отправка Telegram по новому матчу.

Отделено от матчинга (queue/matching.py) в свою очередь `goszakup_notify`,
чтобы HTTP к Telegram и его ретраи не тормозили и не роняли подсчёт матчей
(правило #7). `match_actor` ставит сюда задачу только на forward-потоке
(новый/переанализированный лот), когда matched=True и уведомление ещё не
слалось (UserLotMatch.notified_at IS NULL).

Дедуп — по `notified_at`: actor идемпотентен, повторный enqueue ничего не
шлёт. Уведомление НЕ уходит, если пользователь выключил `notify_telegram`
или не сохранил chat_id.

ВАЖНО (CLAUDE.md, goszakup-worker.service): очередь `goszakup_notify` должна
быть в `--queues` воркера — иначе задачи молча копятся в Redis.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import dramatiq
from sqlalchemy import select

from ..classify.usage import record_call
from ..db.engine import SessionLocal
from ..db.models import Lot, User, UserLotMatch
from ..notify.render import build_explain_keyboard, build_explain_message, build_match_message
from ..notify.telegram import edit_message, send_message
from .broker import broker  # noqa: F401 — импорт broker до actor'а обязателен

log = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="goszakup_notify",
    max_retries=3,
    min_backoff=10_000,
    max_backoff=5 * 60 * 1000,
    time_limit=60 * 1000,
)
def notify_actor(match_id: int) -> None:
    with SessionLocal() as session:
        match = session.get(UserLotMatch, match_id)
        if match is None or not match.matched or match.notified_at is not None:
            return

        query = match.query
        lot = match.lot
        if query is None or lot is None:
            return

        user = session.get(User, query.user_id)
        if user is None or not user.notify_telegram or not user.telegram_chat_id:
            # Пользователь не подключил/выключил уведомления — это не ошибка,
            # помечаем как «обработано», чтобы не дёргать повторно.
            match.notified_at = datetime.now(UTC)
            session.commit()
            return

        text = build_match_message(query, lot, match)
        ok, err = send_message(
            user.telegram_chat_id, text, reply_markup=build_explain_keyboard(lot)
        )
        if not ok:
            # Не проставляем notified_at — Retries добьёт позже (например, при
            # временной недоступности Telegram). Если ошибка постоянная (битый
            # chat_id) — max_retries исчерпается и задача отвалится в DLQ-лог.
            log.warning("notify_actor: не доставлено match=%s: %s", match_id, err)
            raise RuntimeError(f"telegram delivery failed: {err}")

        match.notified_at = datetime.now(UTC)
        session.commit()
        log.info("notify_actor: отправлено match=%s user=%s", match_id, user.id)


@dramatiq.actor(
    queue_name="goszakup_notify",
    # Без ретраев: при ошибке пользователю сразу уходит fallback-сообщение,
    # повтор задачи привёл бы к дублям в чате.
    max_retries=0,
    time_limit=3 * 60 * 1000,
)
def explain_actor(lot_id: int, chat_id: str, placeholder_id: int | None = None) -> None:
    """LLM-объяснение лота по кнопке «Подробнее» из Telegram-уведомления.

    chat_id приходит из callback'а вебхука. Отвечаем только чатам известных
    пользователей — вебхук публичный, callback_data подделываемая.
    placeholder_id — message_id заглушки «⏳ Готовлю…», отправленной вебхуком:
    готовый ответ редактируется в неё (editMessageText), чтобы пользователь
    видел процесс, а не получал молчание на время LLM-вызова.
    """
    from ..classify.llm import explain_lot  # локально: не тянуть LLM-стек при импорте очереди

    def deliver(text: str, *, parse_mode: str = "HTML") -> tuple[bool, str | None]:
        if placeholder_id is not None:
            ok, err = edit_message(chat_id, placeholder_id, text, parse_mode=parse_mode)
            if ok:
                return True, None
            log.warning("explain_actor: edit заглушки не удался (%s), шлю новым", err)
        return send_message(chat_id, text, parse_mode=parse_mode)

    with SessionLocal() as session:
        user = session.execute(
            select(User).where(User.telegram_chat_id == str(chat_id))
        ).scalars().first()
        if user is None:
            log.warning("explain_actor: неизвестный chat_id=%s, игнорирую", chat_id)
            return

        lot = session.get(Lot, lot_id)
        if lot is None:
            deliver("Лот не найден — возможно, он уже удалён из трекера.", parse_mode="")
            return

        try:
            explanation, usage = explain_lot(lot)
        except Exception as e:  # noqa: BLE001 — правило #7: наружу не роняем
            log.warning("explain_actor: LLM не ответил для lot=%s: %s", lot_id, e)
            deliver(
                "Не получилось подготовить объяснение — попробуйте ещё раз "
                "через пару минут или откройте лот в трекере.",
                parse_mode="",
            )
            return

        record_call(session, "explain", usage, lot_id=lot.id, user_id=user.id)
        session.commit()

        ok, err = deliver(build_explain_message(lot, explanation))
        if not ok:
            log.warning("explain_actor: не доставлено lot=%s chat=%s: %s", lot_id, chat_id, err)
