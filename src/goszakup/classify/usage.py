"""Учёт расхода токенов LLM — извлечение `usage` и запись `LlmCall`.

Запись defensive: учёт не должен ронять пайплайн (правило #7). Любая ошибка
здесь — `log.warning`, наружу не уходит. Без `usage` (старый SDK, ошибка до
получения ответа) строку не пишем.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..db.models import LlmCall

log = logging.getLogger(__name__)


def usage_from_response(resp, model: str) -> dict | None:
    """Достать токены из ответа Cerebras (OpenAI-формат: resp.usage.*)."""
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return None
        return {
            "model": model,
            "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
        }
    except Exception as e:  # noqa: BLE001 — учёт не критичен
        log.warning("usage_from_response failed: %s", e)
        return None


def record_call(
    session: Session,
    kind: str,
    usage: dict | None,
    *,
    lot_id: int | None = None,
    user_id: int | None = None,
) -> None:
    """Записать один LLM-вызов. usage=None → no-op (вызова без токенов не было)."""
    if not usage:
        return
    try:
        session.add(
            LlmCall(
                kind=kind,
                model=usage.get("model"),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                lot_id=lot_id,
                # dev-аноним админ имеет id=0 без строки в users — пишем как NULL,
                # чтобы не плодить «фантомного» пользователя в отчёте.
                user_id=user_id or None,
            )
        )
        session.flush()
    except Exception as e:  # noqa: BLE001
        log.warning("record_call(%s) failed: %s", kind, e)
