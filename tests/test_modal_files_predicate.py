"""Предикат `is_tz_like_name` — основа решения «качать ли документ за Перейти»."""

from __future__ import annotations

from goszakup.scraper.modal_files import is_tz_like_name


def test_recognises_technical_specification():
    assert is_tz_like_name("Техническая спецификация")
    assert is_tz_like_name("ТЕХНИЧЕСКАЯ СПЕЦИФИКАЦИЯ (приложение №1)")


def test_recognises_competition_doc():
    assert is_tz_like_name("Конкурсная документация")


def test_recognises_tz_abbreviation():
    assert is_tz_like_name("ТЗ на разработку портала")


def test_recognises_techspec_filename():
    assert is_tz_like_name("techspec_2025_01_anno_123.pdf")


def test_rejects_signature_and_unrelated():
    # Подпись/ЭЦП — мимо.
    assert not is_tz_like_name("Электронная цифровая подпись документа")
    # Договор и заявка — не ТЗ.
    assert not is_tz_like_name("Проект договора")
    assert not is_tz_like_name("Форма заявки на участие")
    # Пустой / None — false.
    assert not is_tz_like_name("")
    assert not is_tz_like_name(None)
