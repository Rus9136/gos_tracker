"""Пре-фильтр пользовательского запроса (`UserQuery.compiled_filters`).

Дешёвый детерминированный отсев ДО LLM: NL-текст запроса решает «подходит ли
лот по смыслу», пре-фильтр — «стоит ли вообще смотреть на этот лот». У него
две роли:

1) вход в watchlist (`watchlist.py`) — запрос с пре-фильтром втягивает лоты
   в дорогие стадии (документы + LLM), запрос без пре-фильтра не втягивает
   ничего и матчится только по уже проанализированным лотам;
2) сужение fan-out'а матчера — не звать LLM на парах, которые пользователь
   заведомо не имел в виду.

Схема (все поля опциональны, хранится в JSON-колонке):

    {"categories": ["it"], "code_prefixes": ["266"],
     "keywords": ["сервер"], "max_amount": 50000000}

Семантика: И между заполненными полями, ИЛИ внутри списка.

Пара функций «SQL-условия + Python-предикат» — как в `scope.py`, но с одним
принципиальным отличием: **авторитетен Python**, а `prefilter_conditions`
даёт заведомое НАДМНОЖЕСТВО (recall-safe). `keywords` в SQL не выражаются
намеренно: на SQLite `lower()` работает только с ASCII, поэтому `ilike` по
кириллице там даёт подмножество — SQL молча терял бы валидные лоты. SQL —
грубый отбор кандидатов, финальное слово за `lot_passes_prefilter`.

Отсутствие пре-фильтра (`None`) здесь означает «ограничений нет»: предикат
возвращает True, условий нет. Требование «запрос без пре-фильтра не
расширяет watchlist» живёт в `watchlist.py` — он просто не строит правило.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import or_

from .classify.verticals import VERTICAL_BY_SLUG
from .db.models import Lot, PlanPoint

# Слишком короткое слово ловит пол-рынка («и», «с») — почти наверняка опечатка
# или мусор от разделителя, поэтому это ошибка ввода, а не тихий скип.
MIN_KEYWORD_LEN = 2


class PrefilterError(ValueError):
    """Некорректный ввод пре-фильтра — web-слой превращает в ?error=."""


def _split(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    return [str(item).strip() for item in raw if str(item).strip()]


def _norm_categories(raw) -> list[str]:
    out = []
    for slug in _split(raw):
        if slug not in VERTICAL_BY_SLUG:
            raise PrefilterError(f"неизвестная вертикаль: {slug}")
        out.append(slug)
    return out


def _norm_prefixes(raw) -> list[str]:
    # Код ЕНС ТРУ пишут и как "26.6", и как "266" — приводим к цифрам, как
    # это делает реестр вертикалей (_PREFIX_TABLE). Заодно снимает риск
    # LIKE-инъекции (`%`/`_` в префиксе).
    out = []
    for prefix in _split(raw):
        digits = prefix.replace(".", "").replace(" ", "")
        if not digits.isdigit():
            raise PrefilterError(f"префикс кода ЕНС ТРУ — только цифры: {prefix}")
        out.append(digits)
    return out


def _norm_keywords(raw) -> list[str]:
    out = []
    for word in _split(raw):
        if len(word) < MIN_KEYWORD_LEN:
            raise PrefilterError(f"слишком короткое ключевое слово: {word}")
        out.append(word.lower())
    return out


def _norm_amount(raw) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise PrefilterError(f"максимальная сумма — целое число: {raw}") from None
    if value <= 0:
        raise PrefilterError("максимальная сумма должна быть больше нуля")
    return value


def normalize_prefilter(raw: dict | None) -> dict | None:
    """Привести ввод к каноническому виду; пустой результат → None.

    Списки сортируются и дедуплицируются, чтобы одинаковый по смыслу ввод
    давал побайтово одинаковый JSON (сравнение «изменился ли пре-фильтр»).
    """
    if not raw:
        return None
    out: dict = {}
    for key, norm in (
        ("categories", _norm_categories),
        ("code_prefixes", _norm_prefixes),
        ("keywords", _norm_keywords),
    ):
        values = sorted(set(norm(raw.get(key))))
        if values:
            out[key] = values
    max_amount = _norm_amount(raw.get("max_amount"))
    if max_amount is not None:
        out["max_amount"] = max_amount
    return out or None


def _passes(
    pf: dict | None,
    *,
    category: str | None,
    enstru_code: str | None,
    text: str,
    amount: Decimal | float | None,
) -> bool:
    """Предикат на плоских значениях — общий для лота и пункта плана."""
    if not pf:
        return True

    categories = pf.get("categories")
    if categories and category not in categories:
        return False

    prefixes = pf.get("code_prefixes")
    if prefixes:
        # Код вида "192021.530.000001" — раздел КПВЭД в первом сегменте.
        head = (enstru_code or "").split(".", 1)[0]
        if not head or not any(head.startswith(p) for p in prefixes):
            return False

    keywords = pf.get("keywords")
    if keywords:
        haystack = text.lower()
        if not any(word in haystack for word in keywords):
            return False

    max_amount = pf.get("max_amount")
    if max_amount is not None:
        if amount is None or Decimal(str(amount)) > Decimal(max_amount):
            return False

    return True


def lot_passes_prefilter(pf: dict | None, lot: Lot) -> bool:
    """Авторитетный предикат пре-фильтра. `pf is None` = ограничений нет."""
    return _passes(
        pf,
        category=lot.category,
        enstru_code=lot.enstru_code,
        text=f"{lot.name or ''} || {lot.enstru or ''}",
        amount=lot.plan_amount,
    )


def plan_passes_prefilter(pf: dict | None, point) -> bool:
    """Тот же пре-фильтр по пункту годового плана (правило #26).

    Поля плана называются иначе, чем у лота (`amount`, `enstru_name`), а
    характеристика (`description`) идёт в поиск по ключевым словам: у пункта
    плана название часто односложное («Ноутбук»), и без неё keywords почти
    не срабатывают.
    """
    return _passes(
        pf,
        category=point.category,
        enstru_code=point.enstru_code,
        text=f"{point.name or ''} || {point.description or ''} "
        f"|| {point.enstru_name or ''}",
        amount=point.amount,
    )


def prefilter_conditions(pf: dict | None) -> list:
    """Recall-safe надмножество пре-фильтра для WHERE (см. докстринг модуля).

    `keywords` сюда не попадают — SQL по ним не эквивалентен Python-предикату
    на SQLite. Результат всегда нужно дофильтровать `lot_passes_prefilter`.
    """
    if not pf:
        return []
    conds = []
    if categories := pf.get("categories"):
        conds.append(Lot.category.in_(categories))
    if prefixes := pf.get("code_prefixes"):
        conds.append(or_(*[Lot.enstru_code.like(f"{p}%") for p in prefixes]))
    if (max_amount := pf.get("max_amount")) is not None:
        conds.append(Lot.plan_amount <= Decimal(max_amount))
    return conds


def plan_prefilter_conditions(pf: dict | None) -> list:
    """То же надмножество для пунктов плана (дофильтровать
    `plan_passes_prefilter`)."""
    if not pf:
        return []
    conds = []
    if categories := pf.get("categories"):
        conds.append(PlanPoint.category.in_(categories))
    if prefixes := pf.get("code_prefixes"):
        conds.append(or_(*[PlanPoint.enstru_code.like(f"{p}%") for p in prefixes]))
    if (max_amount := pf.get("max_amount")) is not None:
        conds.append(PlanPoint.amount <= Decimal(max_amount))
    return conds
