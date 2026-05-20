"""Rule-based классификатор лота — программная замена LLM-вызова там,
где категория и риски определяются ключевыми словами уверенно.

Идея: для каждой dev_category правила выдают «голоса» с весом. Категория
с наибольшим суммарным весом побеждает, confidence = score(winner) / sum.
Если confidence < CONFIDENCE_THRESHOLD — пайплайн зовёт LLM как раньше.

`it_category` (pre-filter из classify/it.py) даёт первичный prior — он уже
посчитан при парсинге лота и хорошо коррелирует с верхним уровнем.

Эта классификация:
- быстрая (десятки мкс на лот),
- не требует LLM,
- покрывает «очевидные» лоты: явное железо, чистый веб-сайт, поставка
  ноутбуков, мобильное приложение и т.п.

`tz_summary` правилами **не генерируется** — он остаётся за LLM или
extractive-этапом (см. этап 4). Когда правила уверены — записываем
анализ без `tz_summary` (поле остаётся NULL).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

# Версия меняется при правках весов/регексов — старые rules-записи будут
# переанализированы при следующем прогоне.
RULES_VERSION = "rules-v1-ru"

# Минимальная уверенность правил для пропуска LLM. Консервативно 0.85 —
# сомнительные случаи (один правил-голос + prior) уходят в LLM. Калибровка
# на проде: правила покрывают чистые «hardware/it_infra/web» с явными
# ключевиками, общие «Программное обеспечение»/«Услуги ИТ» → LLM.
CONFIDENCE_THRESHOLD = 0.85


# === dev_category ===

DevCategory = Literal[
    "1c_development",
    "web_development",
    "mobile_dev",
    "integration",
    "software_support",
    "it_infra",
    "hardware",
    "not_dev",
]

# Prior по IT-категории (pre-filter из classify/it.py). Без текста ТЗ
# уже даёт грубый ответ; ниже регексы уточняют.
_IT_CATEGORY_PRIOR: dict[str, dict[str, float]] = {
    "Оборудование": {"hardware": 3.0, "it_infra": 0.5},
    "Связь и интернет": {"it_infra": 3.0, "software_support": 0.3},
    "ПО и лицензии": {"software_support": 2.0, "1c_development": 0.4, "web_development": 0.4},
    "Услуги ИТ": {},  # неопределённость — полностью полагаемся на текст
}

# (regex, category, weight). Регексы case-insensitive по слою name+extra+tz_text.
# Веса калибровались на здравом смысле: «1С + разработ» — почти железно,
# «программное обеспечение» — слишком общее.
_DEV_CATEGORY_RULES: list[tuple[re.Pattern[str], DevCategory, float]] = [
    # 1С: разработка vs сопровождение разводится по ключевым глаголам
    (
        re.compile(
            r"\b1с\b[^.]{0,120}\b(разработ|доработ|внедрен|конфигурир|программирован|настройк|автоматизац)",
            re.IGNORECASE,
        ),
        "1c_development",
        4.0,
    ),
    (
        re.compile(r"\b1с[:\- ]?предприят|конфигуратор\s*1с|\b1с[:\- ](бп|зуп|упп|ерп)\b", re.IGNORECASE),
        "1c_development",
        3.0,
    ),
    (
        re.compile(
            r"\b1с\b[^.]{0,120}\b(сопровожден|обслужива|поддержк|абонентск)",
            re.IGNORECASE,
        ),
        "software_support",
        3.5,
    ),
    # Веб. Русские словоформы: «портал/портала/портале» — нужен \w* в хвосте,
    # \b в конце не подходит (после «портал» идёт ещё буква и \b не сработает).
    (
        re.compile(
            r"\bсайт\w*|\bпортал\w*|\bвеб[-\s]?приложен\w*|интернет[-\s]?магазин|\blanding|лэндинг|\bcms\b|wordpress|drupal|joomla|magento|битрикс24|bitrix",
            re.IGNORECASE,
        ),
        "web_development",
        3.0,
    ),
    # Мобайл
    (
        re.compile(
            r"мобильн\w+\s+приложен\w*|\bios\b|\bandroid\b|\bflutter\b|react[\s-]?native|\bkotlin\b|\bswift\b",
            re.IGNORECASE,
        ),
        "mobile_dev",
        3.0,
    ),
    # Интеграция
    (
        re.compile(
            r"интеграц\w*|шин\w+\s+данных|api[-\s]?шлюз|обмен\s+данны\w*|\betl\b|\besb\b",
            re.IGNORECASE,
        ),
        "integration",
        2.5,
    ),
    # Поддержка ПО / лицензии
    (
        re.compile(
            r"сопровожден\w*|техническ\w+\s+поддержк\w*|абонентск\w+\s+обслуж\w*|продление\s+лицензи\w*|антивирус",
            re.IGNORECASE,
        ),
        "software_support",
        2.0,
    ),
    # ИТ-инфра. Вес выше hardware-prior'а «Оборудование», чтобы
    # «видеонаблюдение» с it-cat=Оборудование не уходило в hardware.
    (
        re.compile(
            r"видеонаблюден\w*|\bскуд\b|программно[-\s]?аппаратн\w*|сетев\w+\s+безопасн\w*|центр\s+обработки\s+данных|\bцод\b|серверн\w+\s+(инфраструктур\w*|оборудован\w*)|администрирован\w+\s+сет|интернет",
            re.IGNORECASE,
        ),
        "it_infra",
        4.0,
    ),
    # Железо (товары). Хвостовые формы через \w*: ноутбуков/мониторов/принтеров.
    (
        re.compile(
            r"\b(ноутбук\w*|компьютер\w*|монитор\w*|принтер\w*|сканер\w*|мфу|коммутатор\w*|маршрутизатор\w*|термопринтер\w*|планшет\w*|телефон\w*\s+сотов\w*|оргтехник\w*)",
            re.IGNORECASE,
        ),
        "hardware",
        3.0,
    ),
    # Не разработка ПО
    (
        re.compile(
            r"проектно[-\s]?сметн\w*|канализац\w*|вентиляц\w*|благоустройств\w*|строительн\w*[-\s]?монтаж\w*|консультац\w+\s+(услуг|по)|обучен\w*|семинар\w*|тренинг",
            re.IGNORECASE,
        ),
        "not_dev",
        3.0,
    ),
]


@dataclass
class RuleBasedResult:
    dev_category: DevCategory
    tech_stack: list[str]
    solo_feasible: bool
    vendor_lock_risk: Literal["low", "medium", "high"]
    analysis_confidence: Literal["high", "low"]
    confidence: float  # 0..1, для решения «писать или LLM»


# === tech_stack ===

# Фиксированный словарь технологий. Имя слева — каноническая форма (как
# попадёт в tech_stack); regex справа — что искать в тексте.
_TECH_STACK: dict[str, re.Pattern[str]] = {
    "1С": re.compile(r"\b1с\b(?![-\s]?битрикс)", re.IGNORECASE),
    "1С-Битрикс": re.compile(r"\b1с[-\s]?битрикс|bitrix\b|битрикс24", re.IGNORECASE),
    "WordPress": re.compile(r"\bwordpress\b", re.IGNORECASE),
    "Drupal": re.compile(r"\bdrupal\b", re.IGNORECASE),
    "Joomla": re.compile(r"\bjoomla\b", re.IGNORECASE),
    "Magento": re.compile(r"\bmagento\b", re.IGNORECASE),
    "Laravel": re.compile(r"\blaravel\b", re.IGNORECASE),
    "Django": re.compile(r"\bdjango\b", re.IGNORECASE),
    "FastAPI": re.compile(r"\bfastapi\b", re.IGNORECASE),
    "Flask": re.compile(r"\bflask\b", re.IGNORECASE),
    "Spring": re.compile(r"\bspring\b(?!\s*offer)", re.IGNORECASE),
    "ASP.NET": re.compile(r"\basp\.?net\b|\.net\s*core|\.net\s*framework", re.IGNORECASE),
    "React": re.compile(r"\breact\b(?!\s*native)", re.IGNORECASE),
    "Vue": re.compile(r"\bvue\.?js\b|\bvue\b\s+\d", re.IGNORECASE),
    "Angular": re.compile(r"\bangular\b", re.IGNORECASE),
    "Android": re.compile(r"\bandroid\b", re.IGNORECASE),
    "iOS": re.compile(r"\bios\b", re.IGNORECASE),
    "Flutter": re.compile(r"\bflutter\b", re.IGNORECASE),
    "React Native": re.compile(r"\breact[-\s]?native\b", re.IGNORECASE),
    "Kotlin": re.compile(r"\bkotlin\b", re.IGNORECASE),
    "Swift": re.compile(r"\bswift\b(?!\s*(of|order))", re.IGNORECASE),
    "PostgreSQL": re.compile(r"\bpostgre(sql)?\b", re.IGNORECASE),
    "MySQL": re.compile(r"\bmysql\b", re.IGNORECASE),
    "Oracle": re.compile(r"\boracle\b", re.IGNORECASE),
    "MS SQL": re.compile(r"\bms\s*sql\b|sql\s*server", re.IGNORECASE),
    "MongoDB": re.compile(r"\bmongo(db)?\b", re.IGNORECASE),
    "Redis": re.compile(r"\bredis\b", re.IGNORECASE),
    "Docker": re.compile(r"\bdocker\b", re.IGNORECASE),
    "Kubernetes": re.compile(r"\bkubernetes\b|\bk8s\b", re.IGNORECASE),
    "Linux": re.compile(r"\blinux\b|\bubuntu\b|\bcentos\b|\bdebian\b", re.IGNORECASE),
    "Python": re.compile(r"\bpython\b", re.IGNORECASE),
    "Java": re.compile(r"\bjava\b(?!\s*script)", re.IGNORECASE),
    "PHP": re.compile(r"\bphp\b", re.IGNORECASE),
    "Go": re.compile(r"\bgolang\b|\bgo\s+1\.[0-9]+\b", re.IGNORECASE),
    "C#": re.compile(r"\bc#|c\s*sharp\b", re.IGNORECASE),
    "JavaScript": re.compile(r"\bjavascript\b|\bjs\b", re.IGNORECASE),
    "TypeScript": re.compile(r"\btypescript\b", re.IGNORECASE),
}


def _detect_tech_stack(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for name, pat in _TECH_STACK.items():
        if pat.search(text):
            found.append(name)
    return found


# === vendor_lock_risk ===

# Признаки заточки под конкретного поставщика (см. промпт LLM):
# короткое размытое название, отсылки к существующей системе,
# «согласно технической документации» без конкретики.
_VENDOR_LOCK_HIGH_PATTERNS = [
    re.compile(r"согласно\s+техническ\w+\s+документац", re.IGNORECASE),
    re.compile(r"согласно\s+тз|согласно\s+техническому\s+заданию", re.IGNORECASE),
    re.compile(
        r"существующ\w+\s+(информацион\w+\s+систем|ис|программн\w+\s+комплекс)",
        re.IGNORECASE,
    ),
    re.compile(r"уже\s+(внедрённ|внедренн|развёрнут|развернут)", re.IGNORECASE),
    re.compile(r"эксклюзивн\w+\s+(лицензи|правообладат)", re.IGNORECASE),
    re.compile(
        r"патентованн\w+|единственн\w+\s+(производител|поставщик|правообладат)",
        re.IGNORECASE,
    ),
]


def _vendor_lock_risk(name: str | None, text: str) -> Literal["low", "medium", "high"]:
    n = (name or "").strip()
    # 1) Короткое название без конкретики (цифр/латиницы) → high.
    # Цифры и латинские буквы — надёжный признак «технического» названия
    # (модель железа, версия ПО, бренд). Их отсутствие в коротком названии
    # обычно означает бюрократическую формулировку вроде «Обеспечение ХХХ».
    words = re.findall(r"\w+", n)
    if 0 < len(words) <= 5 and not re.search(r"[A-Za-z0-9]", n):
        return "high"
    # 2) Прямые отсылки к существующей системе / эксклюзивности
    for pat in _VENDOR_LOCK_HIGH_PATTERNS:
        if pat.search(text):
            return "high"
    # 3) Упоминание конкретного коммерческого продукта/версии → medium
    if re.search(
        r"\b(1с[:\- ]?предприят\s*\d|sap\s+(s/4|hana)|oracle\s+(siebel|ebs|peoplesoft))\b",
        text,
        re.IGNORECASE,
    ):
        return "medium"
    return "low"


# === solo_feasible ===

# Очень грубо: маленький бюджет на код → реально соло; большой или
# инфра/железо → нет. Цифры подобраны из здравого смысла рынка РК 2026.
_SOLO_OK_CATEGORIES = {
    "1c_development",
    "web_development",
    "mobile_dev",
    "integration",
}
_SOLO_SUPPORT_LIMIT_KZT = Decimal("30000000")
_SOLO_DEV_LIMIT_KZT = Decimal("15000000")


def _solo_feasible(category: DevCategory, plan_amount: Decimal | None) -> bool:
    if category in {"hardware", "it_infra", "not_dev"}:
        return False
    if plan_amount is None:
        # Без суммы — оптимистично «можно», но это редкость
        return category in _SOLO_OK_CATEGORIES or category == "software_support"
    if category == "software_support":
        return plan_amount <= _SOLO_SUPPORT_LIMIT_KZT
    if category in _SOLO_OK_CATEGORIES:
        return plan_amount <= _SOLO_DEV_LIMIT_KZT
    return False


# === entry point ===


def classify_lot(
    *,
    lot_name: str | None,
    lot_extra: str | None,
    it_category: str | None,
    plan_amount: Decimal | None,
    announcement_attributes: str | None,
    tz_text: str | None,
) -> RuleBasedResult:
    """Применяет правила и возвращает заполненный RuleBasedResult.

    confidence в результате — это уверенность по dev_category. Если она
    < CONFIDENCE_THRESHOLD, вызывающий код должен пойти в LLM.
    """
    haystack = " ".join(
        s for s in (lot_name, lot_extra, announcement_attributes, tz_text) if s
    )

    scores: dict[str, float] = defaultdict(float)
    if it_category in _IT_CATEGORY_PRIOR:
        for c, w in _IT_CATEGORY_PRIOR[it_category].items():
            scores[c] += w
    for pat, cat, w in _DEV_CATEGORY_RULES:
        if pat.search(haystack):
            scores[cat] += w

    if not scores:
        # Совсем нет сигналов — раздаём всё на not_dev с нулевой уверенностью.
        # LLM решит.
        category: DevCategory = "not_dev"
        confidence = 0.0
    else:
        category = max(scores, key=scores.get)  # type: ignore[assignment]
        total = sum(scores.values())
        confidence = scores[category] / total if total > 0 else 0.0

    tech_stack = _detect_tech_stack(haystack)
    risk = _vendor_lock_risk(lot_name, haystack)
    solo = _solo_feasible(category, plan_amount)

    # analysis_confidence (другое поле, под LLM-схему): high если есть текст ТЗ,
    # low если нет (тогда пайплайн скорее всего пойдёт в LLM из-за низкого
    # confidence в правилах).
    analysis_confidence: Literal["high", "low"] = "high" if tz_text else "low"

    return RuleBasedResult(
        dev_category=category,  # type: ignore[arg-type]
        tech_stack=tech_stack,
        solo_feasible=solo,
        vendor_lock_risk=risk,
        analysis_confidence=analysis_confidence,
        confidence=confidence,
    )
