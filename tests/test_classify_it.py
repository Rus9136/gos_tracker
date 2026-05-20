"""IT-классификатор: точные enstru и regex-фоллбэки."""

from __future__ import annotations

from goszakup.classify.it import classify


def test_exact_enstru_match_oborudovanie():
    assert classify("Ноутбук") == "Оборудование"
    assert classify("Сервер") == "Оборудование"


def test_exact_enstru_match_uslugi():
    assert classify("Услуги по управлению IT-инфраструктурой") == "Услуги ИТ"


def test_keyword_fallback_internet():
    # ENSTRU незнакомый — должно сработать regex по lot_name.
    assert classify("Прочее", "Услуги доступа в интернет") == "Связь и интернет"


def test_keyword_fallback_po():
    assert classify("", "Лицензия на программное обеспечение Microsoft") == "ПО и лицензии"


def test_keyword_fallback_hardware():
    assert classify("", "Поставка компьютеров и мониторов") == "Оборудование"


def test_no_match_returns_none():
    assert classify("Стулья офисные", "Мебель для приёмной") is None
    assert classify(None, None) is None
    assert classify("", "") is None


def test_enstru_with_whitespace_normalised():
    assert classify("  Ноутбук  ") == "Оборудование"
