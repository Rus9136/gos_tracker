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


def _call_api(method: str, payload: dict) -> tuple[bool, str | None, dict | None]:
    """Возвращает (ok, error, result) — result это поле `result` ответа
    Telegram, если оно объект (для sendMessage там message с message_id)."""
    token = GZ_TELEGRAM_BOT_TOKEN
    if not token:
        return False, "GZ_TELEGRAM_BOT_TOKEN не задан", None

    try:
        resp = httpx.post(
            _API.format(token=token, method=method),
            json=payload,
            timeout=_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — сеть/таймаут не должны ронять вызов
        log.warning("telegram %s failed: %s", method, e)
        return False, f"сетевая ошибка: {e}", None

    if resp.status_code == 200:
        try:
            result = resp.json().get("result")
        except Exception:  # noqa: BLE001
            result = None
        return True, None, result if isinstance(result, dict) else None

    # Telegram отдаёт причину в JSON `description` — пробрасываем для UI/логов.
    try:
        desc = resp.json().get("description", resp.text)
    except Exception:  # noqa: BLE001
        desc = resp.text
    log.warning("telegram %s API %s: %s", method, resp.status_code, desc)
    return False, f"Telegram API {resp.status_code}: {desc}", None


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
    ok, err, _ = _call_api("sendMessage", payload)
    return ok, err


def send_placeholder(chat_id: str, text: str) -> int | None:
    """Plain-text сообщение-заглушка; возвращает message_id для editMessageText.

    None при любой ошибке — вызывающий тогда просто шлёт финальный текст
    обычным send_message.
    """
    ok, _err, result = _call_api(
        "sendMessage",
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
    )
    if ok and result:
        return result.get("message_id")
    return None


def edit_message(
    chat_id: str, message_id: int, text: str, *, parse_mode: str = "HTML"
) -> tuple[bool, str | None]:
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    ok, err, _ = _call_api("editMessageText", payload)
    return ok, err


def answer_callback_query(callback_query_id: str, text: str | None = None) -> tuple[bool, str | None]:
    """Гасит «часики» на inline-кнопке. Ошибка не критична — просто лог."""
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    ok, err, _ = _call_api("answerCallbackQuery", payload)
    return ok, err


def set_webhook(url: str, secret_token: str) -> tuple[bool, str | None]:
    ok, err, _ = _call_api(
        "setWebhook",
        {
            "url": url,
            "secret_token": secret_token,
            # Нам нужны только нажатия inline-кнопок — обычные сообщения боту
            # не обрабатываем, незачем их и получать.
            "allowed_updates": ["callback_query"],
        },
    )
    return ok, err


def delete_webhook() -> tuple[bool, str | None]:
    ok, err, _ = _call_api("deleteWebhook", {})
    return ok, err
