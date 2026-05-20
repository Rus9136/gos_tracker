"""Тесты extractive_summary."""

from __future__ import annotations

from goszakup.classify.extractive_summary import extract_summary


def test_template_from_lot_name_only():
    s = extract_summary(tz_text=None, lot_name="Поставка ноутбуков", lot_extra=None)
    assert s == "Поставка ноутбуков"


def test_template_combines_name_and_extra():
    s = extract_summary(
        tz_text=None,
        lot_name="Поставка ноутбуков",
        lot_extra="25 штук, гарантия 12 месяцев",
    )
    assert s is not None
    assert "Поставка ноутбуков" in s
    assert "25 штук" in s


def test_returns_none_when_nothing():
    assert extract_summary(tz_text=None, lot_name=None, lot_extra=None) is None
    assert extract_summary(tz_text="", lot_name=None, lot_extra=None) is None


def test_field_extraction_russian():
    tz = """
    Конкурсная документация
    Приложение 3
    Техническая спецификация

    Номер закупки: № 12345-1
    Наименование закупки: Поставка канцелярских товаров
    Наименование лота: Бумага офисная А4
    Краткое описание лота: бумага белая, плотность 80 г/м2
    Количество, объём: 500
    Единица измерения: пачка
    Срок поставки: до 30 декабря 2026 года
    Место поставки: г. Алматы, ул. Абая 10
    """
    s = extract_summary(tz_text=tz, lot_name="Бумага А4", lot_extra=None)
    assert s is not None
    assert "Бумага офисная А4" in s
    assert "500" in s  # объём попал
    assert "30 декабря" in s  # срок попал


def test_field_extraction_kazakh_maps_to_russian_labels():
    """Реальный формат goszakup: казахские заголовки, значения на казахском.

    Мы не переводим значения — но извлекаем их в structured form.
    """
    tz = """
    Конкурстық құжаттамаға
    3-қосымша
    Сатып алудың нөмірі: № 16980834-1
    Сатып алудың атауы: Азық-түлікті сатып алу
    Лоттың нөмірі: № 86666914-КППТСОПО1
    Лоттың атауы : Қант
    Лоттың сипаттауы: құрақты, сусымалы
    Лоттың қысқаша сипаттауы: Қант
    Саны, көлемі: 200
    Өлшем бірлігі: Килограмм
    Жеткізу орны: 551010000, Павлодар облысы
    Жеткізу мерзімі: шартқа қол қойылған күннен бастап 2026 жылдың 31 желтоқсанына дейін
    """
    s = extract_summary(tz_text=tz, lot_name="Қант", lot_extra="құрақты")
    assert s is not None
    # Главное — что мы вытащили значения, а не подняли LLM
    assert "Қант" in s
    assert "200" in s
    assert "Килограмм" in s


def test_section_based_fallback():
    """Если полей нет — должны вытащить из секции."""
    tz = """
    Конкурсная документация

    Предмет закупки

    Разработка корпоративного портала с интеграцией к 1С. Срок 90 дней.
    Команда из двух разработчиков. Гарантийная поддержка 12 месяцев.

    Условия оплаты

    Оплата по факту приёмки.
    """
    s = extract_summary(tz_text=tz, lot_name="Портал", lot_extra=None)
    assert s is not None
    assert "корпоративного портала" in s.lower()
    assert "90 дней" in s


def test_first_paragraph_fallback_skips_header():
    tz = """
    Конкурсная документация
    Приложение 3

    Системой электронного документооборота должны управлять одновременно
    до 500 пользователей. Время отклика интерфейса не более 2 секунд.
    Поддержка ЭЦП. Интеграция через REST API.
    """
    s = extract_summary(tz_text=tz, lot_name="СЭД", lot_extra=None)
    assert s is not None
    # Должны взять второй абзац, а не «Конкурсная документация»
    assert "электронного документооборота" in s.lower()


def test_trim_at_sentence_boundary():
    """Длинный текст обрезается на границе предложения."""
    long_text = (
        "Первое предложение содержит сразу всю необходимую информацию. "
        + "Дальше идёт ещё много пунктов с подробностями. " * 30
    )
    tz = f"""
    Предмет закупки

    {long_text}
    """
    s = extract_summary(tz_text=tz, lot_name="—", lot_extra=None)
    assert s is not None
    assert len(s) <= 400
    # Обрезали на границе — последний символ должен быть точка (или …)
    assert s.endswith(".") or s.endswith("…")
