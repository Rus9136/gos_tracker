"""Синк годового плана: маппинг, версии пунктов, водяной знак, связь с лотом."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Lot, PlanPoint
from goszakup.jobs.plans import (
    link_lots,
    plan_root_from_number,
    plan_watermark,
    sync_plans,
    upsert_plan_point,
)

FIXTURES = Path(__file__).parent / "fixtures" / "api"


def _plans_fixture() -> list[dict]:
    return json.loads((FIXTURES / "plans_listing.json").read_text())


@pytest.fixture
def session():
    init_db()
    with SessionLocal() as s:
        s.query(PlanPoint).delete()
        s.query(Lot).delete()
        s.commit()
        yield s


def _fake_client(points):
    client = MagicMock()
    client.iter_graphql.return_value = iter(points)
    return client


def test_plan_root_from_number():
    assert plan_root_from_number("87491617-ЗЦПнеГЗ1") == 87491617
    assert plan_root_from_number("42793938-ОЛ-ОИ2") == 42793938
    assert plan_root_from_number("без-номера") is None
    assert plan_root_from_number(None) is None


def test_upsert_maps_fields(session):
    p = _plans_fixture()[0]
    assert upsert_plan_point(session, p) == "created"
    session.commit()
    row = session.get(PlanPoint, p["rootrecordId"])
    assert row.point_id == p["id"]
    assert row.year == p["plnPointYear"]
    assert row.customer_bin == p["subjectBiin"]
    assert row.amount == Decimal(str(p["amount"]))
    assert row.month == p["refMonthsId"]
    assert row.status_id == p["refPlnPointStatusId"]
    assert row.status_name == p["RefPlnPointStatus"]["nameRu"]
    assert row.trade_method == p["RefTradeMethods"]["nameRu"]
    # Регион — код из katos.REGIONS по префиксу КАТО точки поставки.
    assert row.kato and len(row.kato) == 9
    # Даты API — алматинские (UTC+5), храним в UTC.
    assert row.created_at.hour == int(p["dateCreate"][11:13]) - 5


def test_new_version_updates_row_and_keeps_initial(session):
    p = dict(_plans_fixture()[0])
    p["amount"] = 100
    upsert_plan_point(session, p)
    session.commit()

    newer = dict(p)
    newer["id"] = p["id"] + 10
    newer["amount"] = 250
    newer["refMonthsId"] = 11
    assert upsert_plan_point(session, newer) == "updated"
    session.commit()

    row = session.get(PlanPoint, p["rootrecordId"])
    assert row.point_id == newer["id"]
    assert row.amount == Decimal("250")
    assert row.month == 11
    assert row.versions == 2
    # «Было → стало» восстановить из API нельзя: старые версии по
    # rootrecordId не ищутся, поэтому первую сумму держим у себя.
    assert row.amount_initial == Decimal("100")
    assert row.month_initial == p["refMonthsId"]


def test_older_version_does_not_overwrite(session):
    p = dict(_plans_fixture()[0])
    p["amount"] = 500
    upsert_plan_point(session, p)
    session.commit()

    older = dict(p)
    older["id"] = p["id"] - 10
    older["amount"] = 1
    assert upsert_plan_point(session, older) is None
    session.commit()
    assert session.get(PlanPoint, p["rootrecordId"]).amount == Decimal("500")


def test_sync_stops_at_watermark(session):
    points = _plans_fixture()  # id по убыванию, как отдаёт OWS
    stats = sync_plans(session, _fake_client(points), stop_at_id=points[1]["id"])
    assert stats.scanned == 1
    assert stats.created == 1
    assert session.query(PlanPoint).count() == 1


def test_watermark_is_max_point_id(session):
    points = _plans_fixture()
    sync_plans(session, _fake_client(points))
    assert plan_watermark(session) == max(p["id"] for p in points)


def test_link_lots_sets_root_and_survives_bad_number(session):
    session.add_all(
        [
            Lot(id=1, number="87491617-ЗЦП1", url="u"),
            Lot(id=2, number="мусор", url="u"),
            Lot(id=3, number="86099233-ОК1", url="u"),
        ]
    )
    session.commit()
    assert link_lots(session, batch=2) == 2
    assert session.get(Lot, 1).plan_root_id == 87491617
    assert session.get(Lot, 3).plan_root_id == 86099233
    # Лот с неразбираемым номером остаётся без привязки и не зацикливает обход.
    assert session.get(Lot, 2).plan_root_id is None
    assert link_lots(session) == 0
