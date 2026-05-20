"""Extractive `tz_summary` без LLM.

Стратегии в порядке приоритета:

1) **Field-based extraction.** Реальные ТЗ на goszakup структурированы как
   набор полей (Лоттың атауы / Наименование лота, Лоттың сипаттауы /
   Описание, Саны / Количество, Жеткізу мерзімі / Срок поставки и т.п.).
   Если в тексте достаточно полей — собираем читаемый summary на русском,
   мапя казахские термины. Это покрывает большинство товарных закупок.

2) **Section-based extraction.** Если поля не нашлись, ищем заголовки
   «Предмет закупки», «Цель», «Назначение», «Описание предмета». Это
   подходит для услуг и разработки, где ТЗ — свободный текст.

3) **First meaningful paragraph.** Первый абзац длиной ≥ 100 символов,
   пропускающий титульную шапку.

4) **Template fallback.** `lot.name + lot.extra`, обрезано до 400 символов.
   Возвращается из верхнего вызова, если все предыдущие стратегии дали None.

Все стратегии возвращают строку или None. Длина ограничена 400 символов
(2-3 предложения, как LLM раньше делал). Текст обрезается на границе
предложения, если возможно.
"""

from __future__ import annotations

import re

_MAX_SUMMARY_CHARS = 400
_MIN_FIELD_VALUE_CHARS = 2
_MIN_PARAGRAPH_CHARS = 100

# Поля казахско-русские. Левый ключ — каноническая русская подпись,
# regex'ы — варианты на двух языках. Порядок важен: «краткое описание»
# должно матчиться раньше «описание».
_FIELDS: list[tuple[str, re.Pattern[str]]] = [
    # Наименование ЛОТА (приоритет — это специфическое, что покупают).
    # В реальных ТЗ перед «:» бывает пробел: «Лоттың атауы : Қант».
    (
        "Наименование",
        re.compile(
            r"^\s*(?:Лот(?:ты|тың|та)?\s+атау\w*|Наименован\w*\s+лот\w*)\s*:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    # Наименование ЗАКУПКИ — родительский уровень, fallback если лот не нашёлся
    (
        "Наименование закупки",
        re.compile(
            r"^\s*(?:Сатып\s+алудың\s+атауы|Наименован\w*\s+закупк\w*)\s*:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "Краткое описание",
        re.compile(
            r"^\s*(?:Лот(?:ты|тың)?\s+қысқа\w+\s+сипаттауы|Кратк\w+\s+описан\w+\s+лот\w*)\s*:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "Описание",
        re.compile(
            r"^\s*(?:Лот(?:ты|тың)?\s+сипаттауы|Описан\w+\s+лот\w*|Описан\w+\s+предмета|Описан\w+\s+объекта)\s*:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "Количество",
        re.compile(
            r"^\s*(?:Саны\s*,?\s*көлем\w*|Кол(?:ичество|-?во)\s*(?:,\s*объ[её]м)?)\s*:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "Единица",
        re.compile(
            r"^\s*(?:Өлшем\s+бірлігі|Единиц\w+\s+измерен\w+)\s*:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "Срок поставки",
        re.compile(
            r"^\s*(?:Жеткізу\s+мерзімі|Срок\s+(?:поставки|оказан\w+\s+услуг|выполнен\w+\s+работ))\s*:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "Место поставки",
        re.compile(
            r"^\s*(?:Жеткізу\s+орны|Место\s+поставки|Адрес\s+поставки)\s*:\s*(.+)$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
]


# Секции для развёрнутых ТЗ (услуги, разработка). Если ни одно поле не
# нашлось — пробуем эти заголовки.
_SECTION_HEADERS = [
    r"предмет\s+закупк\w*",
    r"предмет\s+договор\w*",
    r"цел\w+\s+(закупк\w*|проекта|договор\w*)",
    r"назначен\w*",
    r"общи\w+\s+(сведения|положени\w*|информац\w*)",
    r"описан\w+\s+(предмета|объекта)\s+закупк\w*",
    r"объект\s+закупк\w*",
    r"наименован\w+\s+(предмета|товар\w*|услуг\w*|работ)",
    r"техническ\w+\s+характеристик\w*",
]
_SECTION_RE = re.compile(
    r"(?im)^\s*(?:\d+[.)]?\s*)?(?:" + "|".join(_SECTION_HEADERS) + r")[\s:.\-—]*$"
)


def _trim(text: str, max_chars: int = _MAX_SUMMARY_CHARS) -> str:
    """Свёрнутые пробелы + обрезка на границе предложения."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    cut = head.rfind(". ")
    if cut > max_chars // 2:
        return head[: cut + 1].strip()
    return head.rsplit(" ", 1)[0] + "…"


def _extract_fields(text: str) -> dict[str, str]:
    """Возвращает словарь {канон. имя поля → значение} для всех найденных полей.

    Значение обрезано: один токен per поле (не вытягиваем многострочные
    портянки — там обычно начинаются длинные технические требования).
    """
    out: dict[str, str] = {}
    for name, pat in _FIELDS:
        if name in out:
            continue
        m = pat.search(text)
        if not m:
            continue
        value = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")
        # Срезаем потенциальный «хвост» — иногда после значения сразу идёт
        # следующее поле без переноса строки. Берём до 200 символов и до
        # первого большого пробела или начала нового поля-метки.
        value = value.split("  ")[0]
        if len(value) >= _MIN_FIELD_VALUE_CHARS:
            out[name] = value[:200]
    return out


def _summary_from_fields(fields: dict[str, str]) -> str | None:
    """Собирает readable summary из извлечённых полей."""
    # Заголовок: предпочтительно «Наименование» (лота — самое специфичное),
    # затем «Описание», «Краткое описание», и только потом «Наименование
    # закупки» (это родительский уровень). Часто все варианты повторяют
    # друг друга, поэтому берём первое непустое.
    head = (
        fields.get("Наименование")
        or fields.get("Описание")
        or fields.get("Краткое описание")
        or fields.get("Наименование закупки")
    )
    if not head:
        return None
    extras: list[str] = []
    # Дополнения добавляем только если они дают новую информацию
    desc = fields.get("Описание") or fields.get("Краткое описание")
    if desc and desc.lower() not in head.lower():
        extras.append(desc)
    qty = fields.get("Количество")
    unit = fields.get("Единица")
    if qty:
        if unit and unit.lower() not in qty.lower():
            extras.append(f"объём: {qty} {unit}")
        else:
            extras.append(f"объём: {qty}")
    deadline = fields.get("Срок поставки")
    if deadline:
        extras.append(f"срок: {deadline}")
    if not extras:
        return _trim(head)
    return _trim(head + ". " + ". ".join(extras))


def _summary_from_section(text: str) -> str | None:
    m = _SECTION_RE.search(text)
    if not m:
        return None
    rest = text[m.end():]
    # Берём до следующего заголовка (двойной перенос строки)
    cut = re.search(r"\n\s*\n", rest)
    if cut:
        rest = rest[: cut.start()]
    rest = rest.strip()
    if len(rest) < _MIN_FIELD_VALUE_CHARS:
        return None
    return _trim(rest)


def _first_meaningful_paragraph(text: str) -> str | None:
    """Скипаем шапку (название документа, реквизиты) и берём первый
    содержательный абзац."""
    paragraphs = re.split(r"\n\s*\n", text)
    for p in paragraphs:
        p = p.strip()
        if len(p) < _MIN_PARAGRAPH_CHARS:
            continue
        # Шапка часто содержит «Конкурс*», «приложение», «к документации»
        if re.match(r"^\s*(конкурс\w+\s+құжаттам|приложение\s+\d|к\s+конкурсной)", p, re.IGNORECASE):
            continue
        return _trim(p)
    return None


def extract_summary(
    *, tz_text: str | None, lot_name: str | None, lot_extra: str | None
) -> str | None:
    """Главный entry-point: возвращает summary или None.

    None означает «совсем ничего не вытащилось» — вызывающий код может
    оставить tz_summary пустым (или зайти в LLM).
    """
    if tz_text:
        fields = _extract_fields(tz_text)
        s = _summary_from_fields(fields)
        if s:
            return s
        s = _summary_from_section(tz_text)
        if s:
            return s
        s = _first_meaningful_paragraph(tz_text)
        if s:
            return s

    # Fallback на метаданные лота — это лучше, чем NULL: пользователь
    # увидит хотя бы название.
    parts: list[str] = []
    if lot_name:
        parts.append(lot_name)
    if lot_extra and (not lot_name or lot_extra.lower() not in lot_name.lower()):
        parts.append(lot_extra)
    if parts:
        return _trim(". ".join(parts))
    return None
