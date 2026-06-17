"""Тонкая обёртка над Telegram Bot API (sendMessage).

Defensive по тому же принципу, что classify/usage.py и LLM-шаг (правило #7):
никогда не бросает наружу — отправка уведомления не должна валить матчинг или
веб-роут. Возвращает (ok, error): ok=True при успешной доставке, иначе error
с человекочитаемой причиной (для кнопки «Отправить тест» на /settings).

Один общий бот на сервис; токен — `config.GZ_TELEGRAM_BOT_TOKEN`. Парс-режим
HTML: сообщения собираются в notify/render.py с экранированием.
"""

from __future__ import annotations

import logging

import httpx

from ..config import GZ_TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10.0


def send_message(chat_id: str, text: str, *, parse_mode: str = "HTML") -> tuple[bool, str | None]:
    token = GZ_TELEGRAM_BOT_TOKEN
    if not token:
        return False, "GZ_TELEGRAM_BOT_TOKEN не задан"
    if not chat_id:
        return False, "chat_id пуст"

    try:
        resp = httpx.post(
            _API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                # Превью ссылок раздувает сообщение и тянет картинки goszakup —
                # для списка лотов это шум.
                "disable_web_page_preview": True,
            },
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — сеть/таймаут не должны ронять вызов
        log.warning("telegram send failed (chat_id=%s): %s", chat_id, e)
        return False, f"сетевая ошибка: {e}"

    if resp.status_code == 200:
        return True, None

    # Telegram отдаёт причину в JSON `description` — пробрасываем для UI/логов.
    try:
        desc = resp.json().get("description", resp.text)
    except Exception:  # noqa: BLE001
        desc = resp.text
    log.warning("telegram API %s (chat_id=%s): %s", resp.status_code, chat_id, desc)
    return False, f"Telegram API {resp.status_code}: {desc}"
