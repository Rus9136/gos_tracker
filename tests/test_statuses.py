"""Справочник статусов: классификация actual/past и safe-обращение."""

from __future__ import annotations

from goszakup.scraper.statuses import (
    ACTUAL_STATUSES,
    PAST_STATUSES,
    SPECIAL_STATUSES,
    STATUS_NAMES,
    is_actual,
    status_name,
    status_tone,
)


def test_status_groups_dont_overlap():
    actual = set(ACTUAL_STATUSES)
    past = set(PAST_STATUSES)
    special = set(SPECIAL_STATUSES)
    assert not (actual & past), "actual и past пересекаются"
    assert not (actual & special), "actual и special пересекаются"
    assert not (past & special), "past и special пересекаются"


def test_status_groups_known():
    # Если кто-то добавил новый код в STATUS_NAMES, но забыл вписать в группу —
    # этот ассерт это поймает.
    grouped = set(ACTUAL_STATUSES) | set(PAST_STATUSES) | set(SPECIAL_STATUSES)
    assert set(STATUS_NAMES.keys()) == grouped


def test_is_actual_truthy_only_for_actual_codes():
    assert is_actual(210) is True  # «Опубликован»
    assert is_actual(360) is False  # «Закупка состоялась»
    assert is_actual(None) is False
    assert is_actual(99999) is False  # незнакомый код


def test_status_name_lookup_and_none():
    assert status_name(360) == "Закупка состоялась"
    assert status_name(None) is None
    assert status_name(99999) is None


def test_status_tone_defaults_to_muted_for_unknown():
    assert status_tone(210) == "ok"
    assert status_tone(360) == "accent"
    assert status_tone(None) == "muted"
    assert status_tone(99999) == "muted"
