"""Вертикали: код первичен, keyword-фоллбэк вторичен, вне реестра — None."""

from goszakup.classify.verticals import (
    VERTICAL_BY_SLUG,
    VERTICAL_LABELS,
    VERTICALS,
    classify_vertical,
)


def test_registry_slugs_unique_and_latin():
    slugs = [v.slug for v in VERTICALS]
    assert len(slugs) == len(set(slugs))
    for slug in slugs:
        assert slug.isascii() and slug.isidentifier()
    assert set(VERTICAL_LABELS) == set(VERTICAL_BY_SLUG) == set(slugs)


def test_code_prefix_it():
    assert classify_vertical("262011.100.000001", "Компьютер", None) == "it"
    assert classify_vertical("620230.000.000000", None, None) == "it"


def test_code_prefix_medicine_not_furniture():
    # 32.5 (мединструменты) длиннее и специфичнее «31»-мебели и не должен
    # съедаться другими короткими префиксами.
    assert classify_vertical("325013.900.000010", None, None) == "medicine"
    assert classify_vertical("211011.200.000000", "Препарат", None) == "medicine"
    # 26.6 — электромедицинское: длиннее «26»-IT, уходит в медицину.
    assert classify_vertical("266012.900.000005", "Литотриптер", None) == "medicine"
    assert classify_vertical("262013.000.000000", "Компьютер", None) == "it"


def test_code_prefix_other_verticals():
    assert classify_vertical("410040.300.000000", None, None) == "construction"
    assert classify_vertical("310012.500.000000", None, None) == "furniture"
    assert classify_vertical("101112.100.000000", None, None) == "food"
    assert classify_vertical("812210.000.000000", None, None) == "cleaning"
    assert classify_vertical("801012.000.000000", None, None) == "security"
    assert classify_vertical("141230.200.000000", None, None) == "ppe"
    assert classify_vertical("329911.900.000000", None, None) == "ppe"
    assert classify_vertical("452011.000.000000", None, None) == "transport"
    assert classify_vertical("493912.000.000000", None, None) == "transport"
    assert classify_vertical("855920.000.000000", None, None) == "education"


def test_code_wins_over_keywords():
    # Код 80.xx («Охрана») + слово «видеонаблюдение», которое взял бы it.py.
    assert (
        classify_vertical("801019.000.000000", "Услуги охраны", "Видеонаблюдение объекта")
        == "security"
    )


def test_it_fallback_absorbs_it_py():
    # Реальные имена из ENSTRU_TO_CATEGORY (exact-словарь it.py), кода нет.
    assert classify_vertical(None, "Услуги по управлению IT-инфраструктурой", None) == "it"
    assert classify_vertical("", "Компьютер", None) == "it"
    # Keyword-правила it.py тоже работают.
    assert classify_vertical(None, "Услуги по доступу к сети Интернет", None) == "it"


def test_keyword_fallback_other_verticals():
    assert classify_vertical(None, "Услуги по уборке помещений", None) == "cleaning"
    assert classify_vertical(None, None, "Охранные услуги для школы") == "security"


def test_unknown_is_none():
    assert classify_vertical(None, "Стулья офисные", "Мебель для приёмной") is None
    # «31»-код всё же мебель — код первичен даже когда имя ни о чём.
    assert classify_vertical("310011.000.000000", "Стулья офисные", None) == "furniture"
    assert classify_vertical("999999.000.000000", "Прочее непонятное", None) is None
    assert classify_vertical(None, None, None) is None
