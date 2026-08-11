"""Синк заявок с ценами: upsert, победители, отбор объявлений к опросу."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from goszakup.api.mapping import almaty_to_utc
from goszakup.db.engine import SessionLocal, init_db
from goszakup.db.models import Announcement, Lot, LotBid
from goszakup.jobs.bids import (
    BidsSyncStats,
    announcements_to_sync,
    apply_winner_from_bid,
    sync_announcement_bids,
    sync_bids,
    upsert_bid,
)

FIXTURES = Path(__file__).parent / "fixtures" / "api"


def _bids_fixture():
    return json.loads((FIXTURES / "bids_listing.json").read_text())


@pytest.fixture
def session():
    init_db()
    with SessionLocal() as s:
        s.query(LotBid).delete()
        s.query(Lot).delete()
        s.query(Announcement).delete()
        s.commit()
        yield s


def _make_lot(session, lot_id: int, anno_id: int, *, application_end=None, **kw):
    if session.get(Announcement, anno_id) is None:
        session.add(
            Announcement(
                id=anno_id, url=f"https://x/{anno_id}", application_end=application_end
            )
        )
    lot = Lot(
        id=lot_id, number=f"L{lot_id}", announcement_id=anno_id,
        name="Лот", url=f"https://x/{anno_id}", **kw,
    )
    session.add(lot)
    session.commit()
    return lot


def _fake_client(apps):
    client = MagicMock()
    client.iter_graphql.return_value = iter(apps)
    return client


def test_upsert_creates_bid_with_price_and_status(session):
    apps = _bids_fixture()
    app = apps[0]
    app_lot = app["AppLots"][0]
    lot = _make_lot(session, app_lot["lotId"], 900001)

    assert upsert_bid(session, lot, app, app_lot) == "created"
    row = session.get(LotBid, app_lot["id"])
    assert row.lot_id == lot.id
    assert float(row.price) == app_lot["price"]
    assert row.status == (app_lot["RefAppStatus"] or {})["nameRu"]
    assert row.supplier_bin == app["supplierBinIin"]
    # Дата приходит алматинской (UTC+5) и должна лечь в UTC. Сравниваем по
    # «стенным часам»: SQLite (dev/CI) таймзону при round-trip теряет, Postgres
    # хранит — общий знаменатель без tzinfo.
    expected = almaty_to_utc(app["dateApply"])
    assert row.date_apply.replace(tzinfo=None) == expected.replace(tzinfo=None)
    # Повтор того же ответа ничего не меняет — ключ TrdAppLots.id.
    assert upsert_bid(session, lot, app, app_lot) is None
    assert session.query(LotBid).count() == 1


def test_upsert_reports_status_change(session):
    apps = _bids_fixture()
    app = apps[0]
    app_lot = dict(app["AppLots"][0], RefAppStatus={"nameRu": "Подано"})
    lot = _make_lot(session, app_lot["lotId"], 900001)
    assert upsert_bid(session, lot, app, app_lot) == "created"
    # Статус вызревает после дедлайна — пересинк должен это увидеть.
    ripened = dict(app_lot, RefAppStatus={"nameRu": "Победитель"})
    assert upsert_bid(session, lot, app, ripened) == "updated"
    assert session.get(LotBid, app_lot["id"]).status == "Победитель"


def test_winner_only_from_winner_status_and_never_overwrites(session):
    apps = _bids_fixture()
    app = apps[0]
    app_lot = app["AppLots"][0]
    lot = _make_lot(session, app_lot["lotId"], 900001)
    upsert_bid(session, lot, app, app_lot)
    row = session.get(LotBid, app_lot["id"])

    row.status = "Подано"
    assert apply_winner_from_bid(lot, row) is False
    assert lot.winner_bin is None

    row.status = "Победитель"
    assert apply_winner_from_bid(lot, row) is True
    assert lot.winner_bin == app["supplierBinIin"]

    # Уже добытого победителя (HTML/договор) не трогаем.
    lot.winner_bin = "000000000000"
    assert apply_winner_from_bid(lot, row) is False
    assert lot.winner_bin == "000000000000"


def test_sync_skips_foreign_lots_and_marks_polled(session):
    apps = _bids_fixture()
    anno_id = 900002
    # Заводим только ОДИН из лотов объявления — второй считается чужим.
    known_lot_id = apps[0]["AppLots"][0]["lotId"]
    _make_lot(session, known_lot_id, anno_id)
    stats = BidsSyncStats()

    sync_announcement_bids(session, _fake_client(apps), anno_id, stats)
    session.commit()

    assert session.query(LotBid).count() == stats.created
    assert {r.lot_id for r in session.query(LotBid)} == {known_lot_id}
    assert session.get(Announcement, anno_id).bids_synced_at is not None


def test_empty_response_still_marks_polled(session):
    """Пустой ответ — самый важный случай для отметки: без неё объявление
    вернулось бы в выборку следующим же прогоном и так по кругу."""
    anno_id = 900003
    lot = _make_lot(session, 700001, anno_id)
    stats = BidsSyncStats()

    sync_announcement_bids(session, _fake_client([]), anno_id, stats)
    session.commit()

    assert stats.bids_seen == 0
    assert session.get(Announcement, anno_id).bids_synced_at is not None
    # Ноль участников — это и есть «конкуренции не было», а не «не знаем».
    assert lot.bids_count == 0


def test_bids_count_counts_participants_and_zeroes_lots_without_bids(session):
    apps = _bids_fixture()
    anno_id = 900005
    contested_id = apps[0]["AppLots"][0]["lotId"]
    _make_lot(session, contested_id, anno_id)
    # Второй лот того же объявления: заявок по нему нет — опросили и пусто.
    quiet = _make_lot(session, 700002, anno_id)
    assert quiet.bids_count is None  # до опроса — «нет данных»

    sync_announcement_bids(session, _fake_client(apps), anno_id, BidsSyncStats())
    session.commit()

    expected = sum(
        1 for a in apps for al in a["AppLots"] if al["lotId"] == contested_id
    )
    assert session.get(Lot, contested_id).bids_count == expected
    assert quiet.bids_count == 0


def test_selection_respects_grace_recheck_and_winner(session):
    now = datetime.now(UTC)
    # a: дедлайн только что — итогов ещё нет, в выборку не берём.
    _make_lot(session, 1, 1001, application_end=now - timedelta(hours=5))
    # b: созрел и ни разу не опрошен — берём.
    _make_lot(session, 2, 1002, application_end=now - timedelta(days=5))
    # c: опрошен вчера — ждём RECHECK.
    _make_lot(session, 3, 1003, application_end=now - timedelta(days=5))
    session.get(Announcement, 1003).bids_synced_at = now - timedelta(hours=20)
    # d: опрошен давно — берём снова.
    _make_lot(session, 4, 1004, application_end=now - timedelta(days=5))
    session.get(Announcement, 1004).bids_synced_at = now - timedelta(days=9)
    # e: победитель уже известен — выбывает навсегда.
    _make_lot(session, 5, 1005, application_end=now - timedelta(days=5))
    session.add(LotBid(id=5001, lot_id=5, status="Победитель"))
    # f: старше горизонта — не состоялся, больше не дёргаем.
    _make_lot(session, 6, 1006, application_end=now - timedelta(days=100))
    session.commit()

    ids = announcements_to_sync(session)
    assert ids == [1002, 1004]  # никогда не опрошенный раньше давно опрошенного


def test_sync_bids_commits_per_announcement(session):
    apps = _bids_fixture()
    anno_id = 900004
    lot_id = apps[0]["AppLots"][0]["lotId"]
    _make_lot(session, lot_id, anno_id, application_end=datetime.now(UTC) - timedelta(days=5))

    client = MagicMock()
    client.iter_graphql.side_effect = lambda *a, **kw: iter(apps)
    stats = sync_bids(session, client, limit=10)

    assert stats.announcements == 1
    assert stats.created == session.query(LotBid).count() > 0
    assert stats.winners_filled >= 1
