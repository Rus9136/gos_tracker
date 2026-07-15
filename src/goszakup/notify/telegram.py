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

_API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 10.0


def _call_api(method: str, payload: dict) -> tuple[bool, str | None]:
    token = GZ_TELEGRAM_BOT_TOKEN
    if not token:
        return False, "GZ_TELEGRAM_BOT_TOKEN не задан"

    try:
        resp = httpx.post(
            _API.format(token=token, method=method),
            json=payload,
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — сеть/таймаут не должны ронять вызов
        log.warning("telegram %s failed: %s", method, e)
        return False, f"сетевая ошибка: {e}"

    if resp.status_code == 200:
        return True, None

    # Telegram отдаёт причину в JSON `description` — пробрасываем для UI/логов.
    try:
        desc = resp.json().get("description", resp.text)
    except Exception:  # noqa: BLE001
        desc = resp.text
    log.warning("telegram %s API %s: %s", method, resp.status_code, desc)
    return False, f"Telegram API {resp.status_code}: {desc}"


def send_message(
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> tuple[bool, str | None]:
    if not chat_id:
        return False, "chat_id пуст"
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        # Превью ссылок раздувает сообщение и тянет картинки goszakup —
        # для списка лотов это шум.
        "disable_web_page_preview": True,
    }
    # parse_mode="" — plain text (для сырых текстов без экранирования).
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _call_api("sendMessage", payload)


def answer_callback_query(callback_query_id: str, text: str | None = None) -> tuple[bool, str | None]:
    """Гасит «часики» на inline-кнопке. Ошибка не критична — просто лог."""
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return _call_api("answerCallbackQuery", payload)


def set_webhook(url: str, secret_token: str) -> tuple[bool, str | None]:
    return _call_api(
        "setWebhook",
        {
            "url": url,
            "secret_token": secret_token,
            # Нам нужны только нажатия inline-кнопок — обычные сообщения боту
            # не обрабатываем, незачем их и получать.
            "allowed_updates": ["callback_query"],
        },
    )


def delete_webhook() -> tuple[bool, str | None]:
    return _call_api("deleteWebhook", {})
