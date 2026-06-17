"""LLM-matcher: сопоставляет NL-предпочтение пользователя (UserQuery) с лотом.

Это слой ПОВЕРХ classify/llm.py. Дорогое «понимание лота» (чтение ТЗ) уже
сделано один раз и лежит в LotAnalysis.tz_summary. Здесь мы матчим дешёво:
против summary + структурных полей лота, БЕЗ повторного чтения документа.
Поэтому вызов на порядок дешевле анализа.

Результат пишется в UserLotMatch и читается на UI чистым SQL — на запросах
интерфейса LLM не дёргается (тот же принцип, что и в llm.py).

Идемпотентность: пропускаем пересчёт, если у пары (query, lot) уже есть
UserLotMatch с тем же query_version И тем же matcher_version.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Lot, UserLotMatch, UserQuery
from ..scraper.katos import region_name
from .llm import DEFAULT_MODEL, dev_category_label, vendor_lock_label
from .usage import record_call, usage_from_response

log = logging.getLogger(__name__)

# Версия меняется при правках промпта/схемы/провайдера — старые UserLotMatch
# с другой matcher_version будут пересчитаны при следующем прогоне.
MATCHER_VERSION = "match-v1-gpt-oss-120b"


# === Pydantic-схема ответа модели ===


class MatchResult(BaseModel):
    matched: bool = Field(description="Подходит ли лот под запрос пользователя")
    score: int = Field(
        ge=0, le=100,
        description="Релевантность 0..100. >=60 обычно означает matched=true",
    )
    reason: str = Field(
        description="СТРОГО НА РУССКОМ. 1-2 предложения: почему подходит или нет",
    )


# === Промпт ===

SYSTEM_PROMPT = """Ты — ассистент по гос.закупкам Казахстана. Пользователь описал
СВОИМИ СЛОВАМИ, какие лоты он хочет видеть. Твоя задача — решить, подходит ли
конкретный лот под этот запрос, и вернуть результат СТРОГО через вызов
инструмента `submit_match`. Без преамбулы, без markdown, без текста вне инструмента.

Правила:
- Опирайся на смысл запроса, а не на дословные совпадения. Если пользователь
  пишет «разработка на 1С» — лоты про продление лицензий 1С без разработки НЕ
  подходят; интеграции/доработки 1С — подходят.
- Учитывай числовые/региональные ограничения из запроса, если они есть
  (сумма, регион, единоличное исполнение и т.п.).
- `score` — это релевантность 0..100. Ставь `matched=true` при score >= 60.
  Если данных мало (нет краткого описания ТЗ) — будь осторожнее, не завышай.
- `reason` — ТОЛЬКО на русском, 1-2 предложения. Коротко объясни решение.
"""

MATCH_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_match",
        "strict": True,
        "description": "Вернуть решение о соответствии лота запросу. Ровно один вызов.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "matched": {"type": "boolean"},
                "score": {"type": "integer"},
                "reason": {
                    "type": "string",
                    "description": "ТОЛЬКО на русском, 1-2 предложения.",
                },
            },
            "required": ["matched", "score", "reason"],
        },
    },
}


# === Сборка пейлоада ===


def _build_user_message(query_text: str, lot: Lot) -> str:
    a = lot.analysis
    lines = [
        f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n{query_text}",
        "",
        "--- ЛОТ ---",
        f"Название: {lot.name or '—'}",
        f"ENSTRU: {lot.enstru or '—'}",
        f"Заказчик: {lot.customer.name if lot.customer else '—'}",
        f"Плановая сумма ₸: {lot.plan_amount or '—'}",
        # Голый КАТО-код модели ни о чём не говорит — даём имя региона.
        f"Регион: {region_name(lot.kato) or lot.kato or '—'}",
        f"IT-категория: {lot.it_category or '—'}",
    ]
    if a is not None:
        lines += [
            f"Dev-категория: {dev_category_label(a.dev_category)}",
            f"Стек: {', '.join(a.tech_stack) if a.tech_stack else '—'}",
            f"Риск vendor-lock: {vendor_lock_label(a.vendor_lock_risk)}",
            f"Реально соло: {'да' if a.solo_feasible else 'нет' if a.solo_feasible is not None else '—'}",
            "",
            "Краткое описание ТЗ:",
            a.tz_summary or "(нет краткого описания — оценивай по полям выше)",
        ]
    else:
        lines.append("Анализ лота отсутствует — оценивай по названию/ENSTRU/сумме.")
    return "\n".join(lines)


@dataclass
class _Outcome:
    result: MatchResult | None
    error: str | None = None
    usage: dict | None = None


def _call_llm(query_text: str, lot: Lot) -> _Outcome:
    api_key = os.environ.get("CEREBRAS_API_KEY")
    if not api_key:
        return _Outcome(None, "CEREBRAS_API_KEY не задан")

    try:
        from cerebras.cloud.sdk import Cerebras
    except ImportError:
        return _Outcome(None, "cerebras-cloud-sdk не установлен")

    model = os.environ.get("GZ_LLM_MODEL", DEFAULT_MODEL)
    client = Cerebras(api_key=api_key)
    user_msg = _build_user_message(query_text, lot)

    # Cerebras free-tier при бурстах отдаёт 429 «queue_exceeded» — ретраим
    # с бэкоффом (как в classify/llm.py), иначе теряем матчи на ровном месте.
    _RETRY_DELAYS = (5.0, 15.0, 30.0)
    resp = None
    last_err: Exception | None = None
    for attempt, delay in enumerate([0.0, *_RETRY_DELAYS]):
        if delay:
            log.info("matcher retry #%d for lot %s after %.0fs", attempt, lot.id, delay)
            import time as _time
            _time.sleep(delay)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                tools=[MATCH_TOOL],
                tool_choice={"type": "function", "function": {"name": "submit_match"}},
                parallel_tool_calls=False,
                reasoning_effort="low",
                max_tokens=1024,
                timeout=60.0,
            )
            break
        except Exception as e:
            last_err = e
            msg = str(e)
            if "429" in msg or "queue_exceeded" in msg or "too_many_requests" in msg:
                continue
            return _Outcome(None, f"Cerebras API call failed: {e}")

    if resp is None:
        return _Outcome(None, f"Cerebras API: 429 после ретраев: {last_err}")

    usage = usage_from_response(resp, model)

    choice = resp.choices[0].message if resp.choices else None
    if choice is None or not choice.tool_calls:
        finish = resp.choices[0].finish_reason if resp.choices else "—"
        return _Outcome(None, f"модель не вызвала tool: finish_reason={finish}", usage)

    try:
        tool_input = json.loads(choice.tool_calls[0].function.arguments)
    except json.JSONDecodeError as e:
        return _Outcome(None, f"невалидный JSON в tool_call.arguments: {e}", usage)

    try:
        parsed = MatchResult.model_validate(tool_input)
    except ValidationError as e:
        return _Outcome(None, f"Pydantic validation failed: {e}", usage)

    return _Outcome(parsed, usage=usage)


# === Точка входа ===


def match_and_save(session: Session, query: UserQuery, lot: Lot) -> bool:
    """Посчитать и сохранить матч (query × lot). True — сделан новый LLM-вызов.

    Не бросает исключения наружу: fan-out не должен валиться из-за одного лота.
    """
    try:
        return _match_inner(session, query, lot)
    except Exception as e:  # последняя линия защиты
        log.exception("match_and_save crashed (query=%s lot=%s): %s", query.id, lot.id, e)
        return False


def _as_utc(dt: datetime) -> datetime:
    # SQLite возвращает naive (хранит UTC по конвенции _now), Postgres — aware.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _match_inner(session: Session, query: UserQuery, lot: Lot) -> bool:
    existing = session.scalar(
        select(UserLotMatch).where(
            UserLotMatch.user_query_id == query.id,
            UserLotMatch.lot_id == lot.id,
        )
    )
    # Матч устаревает не только при правке запроса/матчера, но и при
    # ПЕРЕАНАЛИЗЕ лота (бамп ANALYZER_VERSION, кнопка «Переанализировать»):
    # tz_summary мог измениться, а матч считался против старого.
    analysis = lot.analysis
    stale_analysis = (
        existing is not None
        and analysis is not None
        and analysis.analyzed_at is not None
        and existing.matched_at is not None
        and _as_utc(existing.matched_at) < _as_utc(analysis.analyzed_at)
    )
    if (
        existing is not None
        and not stale_analysis
        and existing.query_version == query.version
        and existing.matcher_version == MATCHER_VERSION
    ):
        return False  # уже актуально

    outcome = _call_llm(query.text, lot)
    record_call(session, "match", outcome.usage, lot_id=lot.id, user_id=query.user_id)
    if outcome.result is None:
        # На ошибке запись не создаём — чтобы при следующем прогоне с рабочим
        # ключом пара переанализировалась (та же логика, что в llm.py).
        log.warning("match skip (query=%s lot=%s): %s", query.id, lot.id, outcome.error)
        return False

    r = outcome.result
    if existing is None:
        existing = UserLotMatch(user_query_id=query.id, lot_id=lot.id)
        session.add(existing)
    existing.matched = r.matched
    existing.score = r.score
    existing.reason = r.reason
    existing.matcher_version = MATCHER_VERSION
    existing.query_version = query.version
    existing.matched_at = datetime.now(UTC)
    session.flush()
    log.info(
        "match query=%s lot=%s -> matched=%s score=%d",
        query.id, lot.id, r.matched, r.score,
    )
    return True
