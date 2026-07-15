"""Гейт 2 (P1): дубли организаций из гонки + частичный уникальный индекс.

Безбиновые организации (заказчики из листинга) идентифицируются именем.
Параллельные прогоны не должны плодить дубли; частичный уникальный индекс
name WHERE bin IS NULL + savepoint в _get_or_create_org это обеспечивают.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from goszakup.db.models import Organization
from goszakup.jobs import run_preset


def test_org_insert_race_falls_back_to_existing(db_session, monkeypatch):
    db_session.add(Organization(name="ООО Ромашка"))
    db_session.commit()

    # Форсируем промах поиска (гонка: на момент проверки орг «не видно»).
    real_find = run_preset._find_org
    state = {"missed": False}

    def fake_find(session, bin_, name):
        if name == "ООО Ромашка" and not state["missed"]:
            state["missed"] = True
            return None
        return real_find(session, bin_, name)

    monkeypatch.setattr(run_preset, "_find_org", fake_find)

    org = run_preset._get_or_create_org(db_session, bin_=None, name="ООО Ромашка")
    db_session.commit()

    assert org is not None
    assert db_session.query(Organization).filter_by(name="ООО Ромашка").count() == 1


def test_partial_unique_index_blocks_dup_no_bin(db_session):
    db_session.add(Organization(name="Дубль"))
    db_session.commit()
    db_session.add(Organization(name="Дубль"))  # второй безбиновый с тем же именем
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_name_allowed_with_different_bin(db_session):
    # Частичный индекс только для bin IS NULL — орг. с БИН могут делить имя.
    db_session.add_all(
        [Organization(name="Одноимённые", bin="111"), Organization(name="Одноимённые", bin="222")]
    )
    db_session.commit()  # не должно бросить
    assert db_session.query(Organization).filter_by(name="Одноимённые").count() == 2
