"""Тесты rule-based классификатора."""

from __future__ import annotations

from decimal import Decimal

from goszakup.classify.rules import (
    CONFIDENCE_THRESHOLD,
    classify_lot,
)


# helper для коротких вызовов
def _c(name=None, extra=None, it_cat=None, amount=None, attrs=None, tz=None):
    return classify_lot(
        lot_name=name,
        lot_extra=extra,
        it_category=it_cat,
        plan_amount=Decimal(amount) if amount is not None else None,
        announcement_attributes=attrs,
        tz_text=tz,
    )


def test_hardware_simple_notebook():
    r = _c(name="Поставка ноутбуков для нужд акимата", it_cat="Оборудование", amount=5_000_000)
    assert r.dev_category == "hardware"
    assert r.confidence >= CONFIDENCE_THRESHOLD
    assert r.solo_feasible is False  # железо — не соло


def test_web_portal():
    r = _c(
        name="Разработка корпоративного портала на платформе 1С-Битрикс",
        it_cat="Услуги ИТ",
        amount=8_000_000,
        tz="Создание корпоративного портала, личный кабинет, документооборот.",
    )
    assert r.dev_category == "web_development"
    assert "1С-Битрикс" in r.tech_stack
    assert r.confidence >= CONFIDENCE_THRESHOLD
    assert r.solo_feasible is True


def test_1c_development_vs_support():
    dev = _c(
        name="Доработка конфигурации 1С:Предприятие 8.3 для учёта основных средств",
        it_cat="ПО и лицензии",
        tz="Доработка и внедрение модуля учёта ОС в 1С",
    )
    assert dev.dev_category == "1c_development"

    support = _c(
        name="Услуги по сопровождению 1С Предприятие, продление лицензии",
        it_cat="ПО и лицензии",
        tz="Абонентское обслуживание системы 1С, без новой функциональности",
    )
    assert support.dev_category == "software_support"


def test_mobile_app():
    r = _c(
        name="Разработка мобильного приложения для iOS и Android",
        it_cat="Услуги ИТ",
        amount=12_000_000,
        tz="Кроссплатформенное приложение на Flutter",
    )
    assert r.dev_category == "mobile_dev"
    assert "Flutter" in r.tech_stack
    assert "iOS" in r.tech_stack
    assert "Android" in r.tech_stack


def test_video_surveillance_is_infra():
    r = _c(
        name="Поставка и монтаж системы видеонаблюдения",
        it_cat="Оборудование",
        tz="Установка камер видеонаблюдения по периметру",
    )
    assert r.dev_category == "it_infra"
    assert r.solo_feasible is False


def test_integration():
    r = _c(
        name="Интеграция корпоративной ИС с системой электронного документооборота",
        it_cat="Услуги ИТ",
        tz="Разработка API-шлюза, обмен данными между двумя ИС.",
    )
    assert r.dev_category == "integration"


def test_not_dev_construction():
    r = _c(
        name="Разработка проектно-сметной документации на ремонт вентиляции",
        it_cat=None,
    )
    assert r.dev_category == "not_dev"


def test_internet_access_is_infra():
    r = _c(
        name="Услуги по доступу к сети Интернет",
        it_cat="Связь и интернет",
    )
    assert r.dev_category == "it_infra"


def test_vendor_lock_high_on_vague_name():
    r = _c(name="Обеспечение функционирования ИС", it_cat="Услуги ИТ")
    assert r.vendor_lock_risk == "high"


def test_vendor_lock_high_on_existing_system_reference():
    r = _c(
        name="Доработка модуля для существующей информационной системы заказчика",
        it_cat="Услуги ИТ",
        tz="Расширение функционала существующей ИС, которая уже внедрена в эксплуатацию",
    )
    assert r.vendor_lock_risk == "high"


def test_vendor_lock_low_on_specific_name():
    r = _c(
        name="Разработка мобильного приложения учёта рабочего времени сотрудников",
        it_cat="Услуги ИТ",
        amount=10_000_000,
    )
    assert r.vendor_lock_risk == "low"


def test_solo_feasible_dev_threshold():
    cheap = _c(
        name="Создание сайта-визитки на WordPress",
        it_cat="Услуги ИТ",
        amount=3_000_000,
    )
    assert cheap.solo_feasible is True

    expensive = _c(
        name="Создание сайта на WordPress",
        it_cat="Услуги ИТ",
        amount=50_000_000,
    )
    assert expensive.solo_feasible is False


def test_low_confidence_returns_below_threshold():
    """Лот без явных признаков должен дать низкий confidence — пайплайн пойдёт в LLM."""
    r = _c(name="Услуги по обеспечению деятельности", it_cat="Услуги ИТ")
    # category может быть любая, главное — confidence низкий
    assert r.confidence < CONFIDENCE_THRESHOLD


def test_tech_stack_detection():
    r = _c(
        name="Веб-приложение",
        it_cat="Услуги ИТ",
        tz="Backend на Python с FastAPI и PostgreSQL, frontend на React и TypeScript. "
        "Деплой через Docker.",
    )
    stack = set(r.tech_stack)
    assert {"Python", "FastAPI", "PostgreSQL", "React", "TypeScript", "Docker"} <= stack


def test_it_category_prior_only():
    """Без текста, только pre-filter Оборудование → hardware с уверенностью."""
    r = _c(name="Поставка", it_cat="Оборудование")
    assert r.dev_category == "hardware"
    # При priors=3.0 и 0.5 → 3.0 / 3.5 ≈ 0.857 — выше порога
    assert r.confidence >= CONFIDENCE_THRESHOLD


# === интеграционный тест: rule-based ветка пропускает LLM ===
# db_session — общая фикстура из conftest.py, чистит таблицы.


def test_analyze_and_save_uses_rules_for_obvious_hardware(db_session, monkeypatch):
    """Очевидный «поставка ноутбуков» → правила, без LLM."""
    from goszakup.classify import llm as llm_mod
    from goszakup.classify.rules import RULES_VERSION
    from goszakup.db.models import Announcement, Document, Lot, LotAnalysis

    ann = Announcement(id=200, url="https://example/200")
    db_session.add(ann)
    db_session.flush()
    lot = Lot(
        id=20,
        announcement_id=200,
        url="https://example/lot/20",
        name="Поставка ноутбуков в количестве 25 штук",
        # Подкатегория для rules-prior больше не хранится на лоте — считается
        # на лету из name («ноутбук» → «Оборудование»); лоту достаточно слага.
        category="it",
        plan_amount=Decimal("5000000"),
    )
    doc = Document(
        announcement_id=200,
        name="Техническая спецификация",
        url="https://example/file/20",
        local_path="/tmp/fake.pdf",
        sha256="sha-20",
    )
    db_session.add_all([lot, doc])
    db_session.flush()

    monkeypatch.setattr(llm_mod, "extract_text", lambda p: "Ноутбук Intel i5, 8 ГБ RAM")

    def _boom(*a, **kw):
        raise AssertionError("LLM не должен вызываться при rule-based ветке")

    monkeypatch.setattr(llm_mod, "_call_llm", _boom)

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    lot = db_session.scalar(
        select(Lot).where(Lot.id == 20).options(
            selectinload(Lot.announcement).selectinload(Announcement.documents)
        )
    )
    assert llm_mod.analyze_and_save(db_session, lot) is True
    db_session.flush()

    analysis = db_session.scalar(select(LotAnalysis).where(LotAnalysis.lot_id == 20))
    assert analysis is not None
    assert analysis.dev_category == "hardware"
    assert analysis.analyzer_version == RULES_VERSION
    assert analysis.reused_from_lot_id is None
    # extractive_summary заполняет tz_summary — минимум из lot.name
    assert analysis.tz_summary is not None
    assert "Поставка ноутбуков" in analysis.tz_summary
