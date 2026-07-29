"""Пре-фильтр запроса (goszakup.prefilter): нормализация и пара предикатов."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from goszakup.db.models import Announcement, Lot
from goszakup.prefilter import (
    PrefilterError,
    lot_passes_prefilter,
    normalize_prefilter,
    prefilter_conditions,
)


def _lot(**kw) -> Lot:
    base = dict(id=1, url="u/1", announcement_id=1)
    base.update(kw)
    return Lot(**base)


def test_normalize_drops_empties_and_sorts():
    pf = normalize_prefilter(
        {
            "categories": ["it", "it"],
            "code_prefixes": "26.6, 62 ,",
            "keywords": ["Сервер", "  ", "хранилище"],
            "max_amount": "50000000",
        }
    )
    assert pf == {
        "categories": ["it"],
        "code_prefixes": ["266", "62"],
        "keywords": ["сервер", "хранилище"],
        "max_amount": 50_000_000,
    }


def test_normalize_empty_input_is_none():
    assert normalize_prefilter(None) is None
    assert normalize_prefilter({}) is None
    assert normalize_prefilter({"keywords": "", "max_amount": ""}) is None


@pytest.mark.parametrize(
    "raw",
    [
        {"categories": ["айти"]},
        {"code_prefixes": "26%"},
        {"keywords": ["и"]},
        {"max_amount": "много"},
        {"max_amount": -5},
    ],
)
def test_normalize_rejects_garbage(raw):
    with pytest.raises(PrefilterError):
        normalize_prefilter(raw)


def test_no_prefilter_means_no_restriction():
    # None = «ограничений нет». Требование «запрос без пре-фильтра не
    # расширяет watchlist» живёт в watchlist.py, а не здесь.
    assert lot_passes_prefilter(None, _lot())
    assert prefilter_conditions(None) == []


def test_fields_are_conjunction_lists_are_disjunction():
    pf = {"categories": ["it"], "keywords": ["сервер", "схд"]}
    assert lot_passes_prefilter(pf, _lot(category="it", name="Поставка СХД"))
    assert not lot_passes_prefilter(pf, _lot(category="it", name="Поставка мебели"))
    assert not lot_passes_prefilter(
        pf, _lot(category="medicine", name="Поставка сервера")
    )


def test_code_prefix_matches_first_segment_and_null_fails():
    pf = {"code_prefixes": ["266"]}
    assert lot_passes_prefilter(pf, _lot(enstru_code="266020.900.000001"))
    assert not lot_passes_prefilter(pf, _lot(enstru_code="620000.100.000001"))
    # Кода нет — лот не проходит: пре-фильтр по коду это «острый инструмент».
    assert not lot_passes_prefilter(pf, _lot(enstru_code=None))


def test_max_amount_mirrors_scope_min_amount():
    pf = {"max_amount": 1_000_000}
    assert lot_passes_prefilter(pf, _lot(plan_amount=500_000))
    assert lot_passes_prefilter(pf, _lot(plan_amount=1_000_000))
    assert not lot_passes_prefilter(pf, _lot(plan_amount=2_000_000))
    assert not lot_passes_prefilter(pf, _lot(plan_amount=None))


def test_keywords_search_name_and_enstru():
    pf = {"keywords": ["маршрутизатор"]}
    assert lot_passes_prefilter(pf, _lot(name=None, enstru="Маршрутизатор сетевой"))
    assert lot_passes_prefilter(pf, _lot(name="Закуп МАРШРУТИЗАТОРОВ", enstru=None))


def test_sql_conditions_are_recall_safe_superset(db_session):
    """SQL — грубый отбор кандидатов, Python — финальное слово.

    Инвариант: множество из SQL ⊇ множества из Python-предиката. Проверяем
    в том числе кириллический keyword в разном регистре — по нему SQL
    условий не строит вовсе (SQLite lower() ASCII-only).
    """
    db_session.add(Announcement(id=1, url="a/1"))
    rows = [
        (1, "it", "266020.900.000001", "Поставка Серверов", 400_000),
        (2, "it", "620000.100.000001", "Разработка ПО", 900_000),
        (3, "it", None, "СЕРВЕР для ЦОД", 500_000),
        (4, "medicine", "266010.000.000002", "Сервер медицинский", 300_000),
        (5, "it", "266020.900.000003", "Поставка мебели", 200_000),
        (6, "it", "266020.900.000004", "Сервер дорогой", 5_000_000),
        (7, None, "266020.900.000005", "Сервер без вертикали", 100_000),
    ]
    for lot_id, cat, code, name, amount in rows:
        db_session.add(
            Lot(
                id=lot_id,
                url=f"u/{lot_id}",
                announcement_id=1,
                category=cat,
                enstru_code=code,
                name=name,
                plan_amount=amount,
            )
        )
    db_session.flush()

    pf = normalize_prefilter(
        {
            "categories": ["it"],
            "code_prefixes": ["266"],
            "keywords": ["сервер"],
            "max_amount": 1_000_000,
        }
    )
    all_lots = list(db_session.scalars(select(Lot)))
    by_python = {lot.id for lot in all_lots if lot_passes_prefilter(pf, lot)}
    by_sql = set(
        db_session.scalars(select(Lot.id).where(*prefilter_conditions(pf))).all()
    )

    assert by_python == {1}
    assert by_python <= by_sql
    # Надмножество, а не равенство: keywords в SQL не выражаются.
    assert 5 in by_sql
