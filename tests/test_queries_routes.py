"""Smoke /queries и /matched.

GZ_NO_AUTH=1 (conftest) → синтетический dev-админ id=0. `match_actor.send`
подменяется, чтобы backfill при создании/правке запроса не ходил в Redis.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goszakup.db.engine import init_db
from goszakup.db.models import Announcement, Lot, UserLotMatch, UserQuery
from goszakup.queue import matching as matching_mod
from goszakup.web.app import app


@pytest.fixture
def client(monkeypatch, db_session):
    init_db()
    db_session.execute(UserLotMatch.__table__.delete())
    db_session.execute(UserQuery.__table__.delete())
    db_session.commit()
    sent: list[tuple] = []
    monkeypatch.setattr(
        matching_mod.match_actor, "send", lambda *a, **k: sent.append(a)
    )
    with TestClient(app) as c:
        c.sent = sent  # type: ignore[attr-defined]
        yield c


def test_queries_page_renders(client):
    r = client.get("/queries")
    assert r.status_code == 200
    assert 'name="text"' in r.text
    assert "Создать запрос" in r.text


def test_create_edit_toggle_delete_query(client, db_session):
    r = client.post(
        "/queries",
        data={"name": "1С", "text": "разработка 1С, до 5 млн"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    q = db_session.query(UserQuery).one()
    assert q.user_id == 0 and q.version == 1 and q.active

    # Правка текста бампает version (инвалидация кеша матчей).
    r = client.post(
        f"/queries/{q.id}/edit",
        data={"name": "1С", "text": "только доработка 1С"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db_session.expire_all()
    assert db_session.get(UserQuery, q.id).version == 2

    # Правка только имени version не трогает.
    client.post(f"/queries/{q.id}/edit", data={"name": "1C new", "text": "только доработка 1С"})
    db_session.expire_all()
    assert db_session.get(UserQuery, q.id).version == 2

    client.post(f"/queries/{q.id}/toggle")
    db_session.expire_all()
    assert db_session.get(UserQuery, q.id).active is False

    client.post(f"/queries/{q.id}/delete")
    db_session.expire_all()
    assert db_session.query(UserQuery).count() == 0


def test_create_with_prefilter_stores_normalized_json(client, db_session):
    client.post(
        "/queries",
        data={
            "name": "СХД",
            "text": "хочу системы хранения",
            "categories": ["it"],
            "keywords": "СХД,  сервер ",
            "code_prefixes": "26.6",
            "max_amount": "5000000",
        },
    )
    q = db_session.query(UserQuery).one()
    assert q.compiled_filters == {
        "categories": ["it"],
        "code_prefixes": ["266"],
        "keywords": ["сервер", "схд"],  # нормализация сортирует
        "max_amount": 5_000_000,
    }
    assert q.version == 1


def test_prefilter_edit_keeps_version_and_drops_stale_matches(client, db_session):
    """Пре-фильтр в промпт матчера не идёт: пересчитывать матчи незачем,
    но те, что новый фильтр не пропускает, надо убрать с «Подходящих»."""
    db_session.add(Announcement(id=101, url="u/101"))
    db_session.add(Lot(id=1, url="u/101", announcement_id=101, name="Сервер",
                       category="it", is_actual=True))
    db_session.add(Lot(id=2, url="u/101", announcement_id=101, name="Стулья",
                       category="furniture", is_actual=True))
    db_session.commit()

    client.post("/queries", data={"name": "q", "text": "хочу серверы"})
    q = db_session.query(UserQuery).one()
    for lot_id in (1, 2):
        db_session.add(UserLotMatch(
            user_query_id=q.id, lot_id=lot_id, matched=True, score=90,
            matcher_version="m1", query_version=1,
        ))
    db_session.commit()

    client.post(f"/queries/{q.id}/edit", data={
        "name": "q", "text": "хочу серверы", "categories": ["it"],
    })
    db_session.expire_all()
    q = db_session.get(UserQuery, q.id)
    assert q.version == 1
    assert q.compiled_filters == {"categories": ["it"]}
    assert [m.lot_id for m in db_session.query(UserLotMatch).all()] == [1]


def test_garbage_prefix_is_rejected_with_error(client, db_session):
    r = client.post(
        "/queries",
        data={"name": "q", "text": "текст", "code_prefixes": "26%"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    assert db_session.query(UserQuery).count() == 0


def test_empty_prefilter_fields_stay_null(client, db_session):
    client.post("/queries", data={
        "name": "q", "text": "текст", "keywords": " , ", "max_amount": "",
    })
    assert db_session.query(UserQuery).one().compiled_filters is None


def test_empty_text_is_rejected(client, db_session):
    client.post("/queries", data={"name": "x", "text": "   "})
    assert db_session.query(UserQuery).count() == 0


def test_foreign_query_404(client, db_session):
    q = UserQuery(user_id=42, name="чужой", text="не мой запрос")
    db_session.add(q)
    db_session.commit()
    r = client.post(
        f"/queries/{q.id}/edit", data={"name": "h", "text": "hack"},
        follow_redirects=False,
    )
    assert r.status_code == 404


def test_matched_page_lists_cached_matches(client, db_session):
    db_session.add(Announcement(id=101, url="u/101"))
    lot = Lot(id=1, url="u/101", announcement_id=101, name="Доработка 1С",
              plan_amount=3_000_000, is_actual=True)
    db_session.add(lot)
    q = UserQuery(user_id=0, name="1С", text="разработка 1С")
    db_session.add(q)
    db_session.flush()
    db_session.add(
        UserLotMatch(
            user_query_id=q.id, lot_id=lot.id, matched=True, score=88,
            reason="прямое совпадение по 1С",
            matcher_version="match-v1-gpt-oss-120b", query_version=1,
        )
    )
    db_session.commit()

    r = client.get("/matched")
    assert r.status_code == 200
    assert "Доработка 1С" in r.text
    assert "прямое совпадение по 1С" in r.text
    assert "88" in r.text

    # Фильтр по несуществующему запросу — пусто, но не 500.
    r = client.get("/matched", params={"query_id": 99999})
    assert r.status_code == 200


def test_matched_page_dedupes_lot_across_queries(client, db_session):
    # Лот, попавший под несколько запросов, на странице показывается один раз
    # (как и бейдж в сайдбаре считает уникальные лоты), с матчем лучшего балла.
    db_session.add(Announcement(id=201, url="u/201"))
    lot = Lot(id=11, url="u/201", announcement_id=201, name="Портал на 1С",
              plan_amount=5_000_000, is_actual=True)
    db_session.add(lot)
    q1 = UserQuery(user_id=0, name="Запрос А", text="разработка")
    q2 = UserQuery(user_id=0, name="Запрос Б", text="внедрение")
    db_session.add_all([q1, q2])
    db_session.flush()
    db_session.add_all([
        UserLotMatch(user_query_id=q1.id, lot_id=lot.id, matched=True, score=70,
                     reason="совпадение по А",
                     matcher_version="match-v1-gpt-oss-120b", query_version=1),
        UserLotMatch(user_query_id=q2.id, lot_id=lot.id, matched=True, score=92,
                     reason="совпадение по Б",
                     matcher_version="match-v1-gpt-oss-120b", query_version=1),
    ])
    db_session.commit()

    r = client.get("/matched")
    assert r.status_code == 200
    # Лот ровно один раз, показан матч с лучшим баллом (92, «Запрос Б»).
    assert r.text.count("Портал на 1С") == 1
    assert "совпадение по Б" in r.text
    assert "совпадение по А" not in r.text
    assert "Доработка 1С" not in r.text


def test_matched_page_limits_to_page_size(client, db_session):
    # 55 сматченных лотов, балл = номер. Страница показывает только top-50 по
    # баллу (LIMIT в SQL), а не весь result set — самый слабый лот отсутствует.
    from goszakup.web.app import PAGE_SIZE

    db_session.add(Announcement(id=900, url="u/900"))
    q = UserQuery(user_id=0, name="все", text="разработка")
    db_session.add(q)
    db_session.flush()
    total = PAGE_SIZE + 5
    for n in range(1, total + 1):
        db_session.add(
            Lot(id=n, url="u/900", announcement_id=900, name=f"MATCHLOT{n:03d}",
                plan_amount=1_000_000, is_actual=True)
        )
        db_session.add(
            UserLotMatch(user_query_id=q.id, lot_id=n, matched=True, score=n,
                         reason="r", matcher_version="m", query_version=1)
        )
    db_session.commit()

    body = client.get("/matched").text
    shown = [n for n in range(1, total + 1) if f"MATCHLOT{n:03d}" in body]
    assert len(shown) == PAGE_SIZE
    assert f"MATCHLOT{total:03d}" in body          # самый высокий балл показан
    assert "MATCHLOT001" not in body               # самый низкий отсечён LIMIT'ом
