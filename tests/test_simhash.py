"""Unit-тесты SimHash и интеграционный тест дедупликации в analyze_and_save."""

from __future__ import annotations

from goszakup.classify.simhash import (
    HAMMING_THRESHOLD,
    from_signed64,
    hamming,
    simhash,
    to_signed64,
)


def test_simhash_deterministic():
    text = "Поставка ноутбуков в количестве 25 штук для нужд акимата"
    assert simhash(text) == simhash(text)


def test_simhash_identical_text():
    a = "Создание корпоративного портала на платформе 1С-Битрикс."
    b = "Создание корпоративного портала на платформе 1С-Битрикс."
    assert hamming(simhash(a), simhash(b)) == 0


def test_simhash_close_texts_below_threshold():
    """Реалистичный ТЗ-шаблон ~5KB с одной точечной заменой должен укладываться в порог.

    На коротких текстах одна замена даёт большой удар по hamming, потому что
    у каждого слова мало shingle-«поддержки». В реальных ТЗ (несколько КБ
    текста) один-два отличия маскируются массой одинаковых shingles.
    """
    base = (
        "Техническая спецификация на поставку компьютерного оборудования. "
        "Процессор Intel Core i5, оперативная память 8 ГБ, SSD 256 ГБ. "
        "Монитор 24 дюйма, разрешение 1920x1080. Клавиатура и мышь в комплекте. "
        "Гарантия 12 месяцев с момента поставки. Срок поставки 30 дней. "
        "Оплата по факту приёмки. Доставка силами поставщика. "
        "Сертификация оборудования по стандартам Республики Казахстан. "
    ) * 10
    a = base + "Количество единиц 25 штук."
    b = base + "Количество единиц 30 штук."
    d = hamming(simhash(a), simhash(b))
    assert d <= HAMMING_THRESHOLD, f"hamming={d}, expected ≤ {HAMMING_THRESHOLD}"


def test_simhash_different_texts_above_threshold():
    a = "Поставка ноутбуков в количестве 25 штук для нужд акимата"
    b = "Услуги по разработке мобильного приложения на платформе iOS"
    d = hamming(simhash(a), simhash(b))
    assert d > HAMMING_THRESHOLD, f"hamming={d}, expected > {HAMMING_THRESHOLD}"


def test_signed_unsigned_roundtrip():
    """Положительные и отрицательные значения должны корректно конвертироваться."""
    for u in [0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1]:
        s = to_signed64(u)
        assert from_signed64(s) == u


def test_hamming_handles_signed_input():
    """XOR-popcount должен работать на отрицательных значениях из BIGINT."""
    a = simhash("aaa bbb ccc ddd eee")
    b = simhash("aaa bbb ccc ddd fff")
    # Прямое сравнение должно совпадать со сравнением через signed-конвертацию.
    d_unsigned = hamming(a, b)
    d_via_signed = hamming(from_signed64(to_signed64(a)), from_signed64(to_signed64(b)))
    assert d_unsigned == d_via_signed


def test_simhash_empty_and_short():
    assert simhash("") == 0
    assert simhash("   ") == 0
    # Один-два токена короче shingle — должны не падать
    assert simhash("один") != 0 or True  # просто не падать
    simhash("один два")


# === интеграционный тест: analyze_and_save копирует анализ при близком simhash ===


def _make_lot(session, lot_id: int, anno_id: int, name: str, doc_text: str, *,
              local_path: str = "/tmp/fake.pdf"):
    """Создаёт минимальный набор записей: announcement, lot, document с заполненным simhash.

    Возвращает (lot, document).
    """
    from goszakup.classify.simhash import simhash, to_signed64
    from goszakup.db.models import Announcement, Document, Lot

    ann = session.get(Announcement, anno_id)
    if ann is None:
        ann = Announcement(id=anno_id, url=f"https://example/{anno_id}")
        session.add(ann)
        session.flush()
    lot = Lot(
        id=lot_id,
        announcement_id=anno_id,
        url=f"https://example/lot/{lot_id}",
        name=name,
        category="it",  # без этого LLM не вызывается → не вызывается и дедуп
    )
    doc = Document(
        announcement_id=anno_id,
        name="Техническая спецификация",
        url=f"https://example/file/{lot_id}",
        local_path=local_path,
        sha256=f"sha-{lot_id}",
        text_simhash=to_signed64(simhash(doc_text)),
    )
    session.add_all([lot, doc])
    session.flush()
    return lot, doc


def test_analyze_and_save_reuses_close_simhash(db_session, monkeypatch):
    """Если у нового лота ТЗ почти идентичен уже проанализированному —
    результат копируется без LLM-вызова."""
    from goszakup.classify import llm as llm_mod
    from goszakup.db.models import LotAnalysis, User

    # Watchlist — функция таблицы users (фаза C): без подписчика на вертикаль
    # analyze_and_save откажет ещё до simhash-дедупликации.
    db_session.add(User(username="u", password_hash="", is_active=True,
                        categories=["it"]))
    db_session.flush()

    # Длинная общая часть (типовой ТЗ-шаблон в реальном размере) + одно
    # точечное отличие. На таком распределении одна замена даёт hamming ≤ 3.
    common = (
        "Техническая спецификация. Разработка корпоративного портала "
        "на платформе 1С-Битрикс. Стек: PHP 8.1, MySQL 8.0, Redis 7. "
        "Требования к функционалу: личный кабинет сотрудника, новостная "
        "лента, документооборот, согласование заявок, отчётность, "
        "интеграция с 1С:Предприятие через REST API. Команда из двух "
        "разработчиков и одного руководителя проекта. Гарантийная "
        "поддержка 12 месяцев с момента подписания акта приёмки. "
        "Сертификация по стандартам Республики Казахстан. Оплата по "
        "факту приёмки этапов согласно календарному графику. "
    ) * 15
    tz_text_a = common + " Срок выполнения 90 дней."
    tz_text_b = common + " Срок выполнения 120 дней."

    lot_a, doc_a = _make_lot(db_session, 1, 100, "Портал 1", tz_text_a)
    lot_b, doc_b = _make_lot(db_session, 2, 101, "Портал 2", tz_text_b)
    db_session.flush()

    # Первичный анализ lot_a — кладём вручную (как будто LLM уже отработал).
    source_analysis = LotAnalysis(
        lot_id=lot_a.id,
        dev_category="web_development",
        tech_stack=["1С-Битрикс", "PHP"],
        tz_summary="Разработка корп-портала на 1С-Битрикс с интеграцией к 1С.",
        solo_feasible=True,
        vendor_lock_risk="medium",
        analysis_confidence="high",
        analyzer_version=llm_mod.ANALYZER_VERSION,
        tz_sha256=doc_a.sha256,
        source_document_id=doc_a.id,
    )
    db_session.add(source_analysis)
    db_session.flush()

    # `_analyze_inner` обращается к extract_text по local_path — замокаем,
    # чтобы не лезть в реальные PDF/DOCX.
    monkeypatch.setattr(llm_mod, "extract_text", lambda p: tz_text_b)
    # LLM не должен дёргаться — но если дёрнется, тест явно упадёт.
    def _boom(*a, **kw):
        raise AssertionError("LLM не должен вызываться при удачной дедупликации")
    monkeypatch.setattr(llm_mod, "_call_llm", _boom)

    # Перезагружаем lot_b с announcement → documents (relationship), чтобы
    # pick_tz_document нашёл документ.
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from goszakup.db.models import Announcement, Lot
    lot_b = db_session.scalar(
        select(Lot).where(Lot.id == 2).options(
            selectinload(Lot.announcement).selectinload(Announcement.documents)
        )
    )

    assert llm_mod.analyze_and_save(db_session, lot_b) is True
    db_session.flush()

    new_analysis = db_session.scalar(
        select(LotAnalysis).where(LotAnalysis.lot_id == 2)
    )
    assert new_analysis is not None
    assert new_analysis.reused_from_lot_id == 1
    assert new_analysis.dev_category == "web_development"
    assert new_analysis.tz_summary == source_analysis.tz_summary
    assert new_analysis.tech_stack == source_analysis.tech_stack
