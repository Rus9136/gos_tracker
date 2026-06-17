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

from ..db.engine import SessionLocal
from ..db.models import User, UserLotMatch
from ..notify.render import build_match_message
from ..notify.telegram import send_message
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
        ok, err = send_message(user.telegram_chat_id, text)
        if not ok:
            # Не проставляем notified_at — Retries добьёт позже (например, при
            # временной недоступности Telegram). Если ошибка постоянная (битый
            # chat_id) — max_retries исчерпается и задача отвалится в DLQ-лог.
            log.warning("notify_actor: не доставлено match=%s: %s", match_id, err)
            raise RuntimeError(f"telegram delivery failed: {err}")

        match.notified_at = datetime.now(UTC)
        session.commit()
        log.info("notify_actor: отправлено match=%s user=%s", match_id, user.id)
