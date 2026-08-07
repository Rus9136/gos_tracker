"""OwsClient: Bearer, независимый rate-limit, OwsAuthError, пагинация."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import fakeredis
import pytest

from goszakup.api.client import API_LIMIT_KEY, OwsApiError, OwsAuthError, OwsClient
from goszakup.queue.rate_limit import LIMIT_KEY


@pytest.fixture
def fake_redis():
    return fakeredis.FakeStrictRedis(decode_responses=True)


def _client(**kw):
    kw.setdefault("token", "test-token")
    kw.setdefault("delay", 0.0)
    return OwsClient(**kw)


def _resp(status=200, text="", payload=None):
    r = MagicMock()
    r.status_code = status
    r.text = text if payload is None else json.dumps(payload)
    r.json.return_value = payload
    return r


def test_token_required(monkeypatch):
    monkeypatch.setattr("goszakup.api.client.OWS_TOKEN", None)
    with pytest.raises(OwsAuthError):
        OwsClient()


def test_bearer_header():
    c = _client()
    assert c.session.headers["Authorization"] == "Bearer test-token"


def test_rate_limit_key_isolated(fake_redis):
    """API-лимитер живёт на своём ключе и не занимает слот HTML-скрейпера."""
    c = _client(redis_client=fake_redis, delay=5.0)
    c.session.request = MagicMock(return_value=_resp(payload={}))
    c.get("/v3/refs/ref_buy_status")
    assert fake_redis.exists(API_LIMIT_KEY)
    assert not fake_redis.exists(LIMIT_KEY)


def test_invalid_route_is_auth_error():
    c = _client()
    c.session.request = MagicMock(
        return_value=_resp(404, text='{"name":"Not Found","message":"Invalid Route"}')
    )
    with pytest.raises(OwsAuthError):
        c.get("/v3/trd-buy")


def test_plain_404_is_not_auth_error():
    c = _client()
    c.session.request = MagicMock(return_value=_resp(404, text="not found"))
    r = c.get("/v3/whatever")
    assert r.status_code == 404


def test_proxies_kwarg_ignored():
    """_proxies_for() из documents.py не должен загнать ows-хост в туннель."""
    c = _client()
    c.session.request = MagicMock(return_value=_resp(payload={}))
    c.get("https://ows.goszakup.gov.kz/download/trd_buy/abc", proxies={"https": "socks5h://x"})
    assert "proxies" not in c.session.request.call_args.kwargs


def test_retry_on_429(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("goszakup.api.client.time.sleep", sleeps.append)
    c = _client()
    c.session.request = MagicMock(side_effect=[_resp(429), _resp(payload={"ok": 1})])
    assert c.get_json("/v3/x") == {"ok": 1}
    assert sleeps == [2]


_ES_THROTTLE_BODY = (
    '{"name":"Elasticsearch Database Exception","message":"Elasticsearch request '
    'failed with code 429. Response body:\\n{\\"error\\":{\\"root_cause\\":[],'
    '\\"type\\":\\"search_phase_execution_exception\\"}}"}'
)


def test_retry_on_elasticsearch_500(monkeypatch):
    """Перегрузку своего ES OWS отдаёт как 500 — это throttle, а не ошибка."""
    sleeps: list[float] = []
    monkeypatch.setattr("goszakup.api.client.time.sleep", sleeps.append)
    c = _client()
    c.session.request = MagicMock(
        side_effect=[_resp(500, text=_ES_THROTTLE_BODY), _resp(payload={"ok": 1})]
    )
    assert c.get_json("/v3/x") == {"ok": 1}
    assert sleeps == [2]


def test_plain_500_is_not_retried(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("goszakup.api.client.time.sleep", sleeps.append)
    c = _client()
    c.session.request = MagicMock(return_value=_resp(500, text="Internal Server Error"))
    with pytest.raises(OwsApiError):
        c.get_json("/v3/x")
    assert sleeps == []


def test_stream_500_body_not_touched(monkeypatch):
    """У stream-ответа .text выкачал бы файл в память — тело не читаем."""
    monkeypatch.setattr("goszakup.api.client.time.sleep", lambda *_: None)
    r = MagicMock()
    r.status_code = 500
    type(r).text = property(lambda self: pytest.fail("тело stream-ответа прочитано"))
    c = _client()
    c.session.request = MagicMock(return_value=r)
    assert c.get("https://ows.goszakup.gov.kz/download/x", stream=True) is r


def test_refs_error_payload():
    c = _client()
    c.session.request = MagicMock(
        return_value=_resp(payload={"error": "Справочник не найден", "code": 404})
    )
    with pytest.raises(OwsApiError):
        c.get_json("/v3/refs/ref_enstru")


def _gql_page(items, *, last_id, has_next):
    return _resp(payload={
        "data": {"TrdBuy": items},
        "extensions": {"pageInfo": {"lastId": last_id, "hasNextPage": has_next}},
    })


def test_iter_graphql_pagination():
    c = _client()
    c.session.request = MagicMock(side_effect=[
        _gql_page([{"id": 3}, {"id": 2}], last_id=2, has_next=True),
        _gql_page([{"id": 1}], last_id=1, has_next=False),
    ])
    got = list(c.iter_graphql("query($after: Int)...", root="TrdBuy", limit=2))
    assert [i["id"] for i in got] == [3, 2, 1]
    # Вторая страница запрошена с курсором after=2.
    second_vars = c.session.request.call_args_list[1].kwargs["json"]["variables"]
    assert second_vars["after"] == 2


def test_iter_graphql_stops_on_stuck_cursor():
    """hasNextPage=true при неподвижном lastId не должен зациклить."""
    c = _client()
    c.session.request = MagicMock(
        return_value=_gql_page([{"id": 5}], last_id=5, has_next=True)
    )
    first = list(c.iter_graphql("q", root="TrdBuy"))
    assert [i["id"] for i in first] == [5, 5]  # стр.1 (after=None) + стр.2 (after=5, lastId==after)
    assert c.session.request.call_count == 2


def test_graphql_page_cache(fake_redis):
    """Повторный тот же запрос с cache_ttl отдаётся из Redis без HTTP.

    Критично для daily: 20 региональных preset'ов ходят по идентичным
    страницам листинга — без кэша они душат друг друга под общим rate-limit.
    """
    c = _client(redis_client=fake_redis)
    c.session.request = MagicMock(return_value=_gql_page([{"id": 1}], last_id=1, has_next=False))
    v = {"f": {"refLotStatusId": [210]}, "limit": 200, "after": None}
    first = c.graphql("q", v, cache_ttl=900)
    second = c.graphql("q", v, cache_ttl=900)
    assert first == second
    assert c.session.request.call_count == 1
    # Другие variables — другой ключ, новый запрос.
    c.graphql("q", {**v, "after": 1}, cache_ttl=900)
    assert c.session.request.call_count == 2
    # Без cache_ttl кэш не трогаем.
    c.graphql("q", v)
    assert c.session.request.call_count == 3


def test_graphql_cache_follower_waits_for_leader(fake_redis, monkeypatch):
    """Второй клиент (фолловер) не дублирует запрос, а ждёт страницу лидера."""
    leader = _client(redis_client=fake_redis)
    follower = _client(redis_client=fake_redis)
    v = {"limit": 200, "after": None}

    # Лидер прошёл: страница в кэше, лок снят.
    leader.session.request = MagicMock(return_value=_gql_page([{"id": 1}], last_id=1, has_next=False))
    leader.graphql("q", v, cache_ttl=900)

    # Фолловер попадает в кэш и вообще не делает HTTP.
    follower.session.request = MagicMock(side_effect=AssertionError("не должен ходить в API"))
    data, _ = follower.graphql("q", v, cache_ttl=900)
    assert data == {"TrdBuy": [{"id": 1}]}

    # Гонка: лок держит «мёртвый» лидер, кэша нет — фолловер ждёт, затем
    # перехватывает лок по истечении и качает сам.
    fake_redis.flushall()
    fake_redis.set("goszakup:api:cache:" + __import__("hashlib").sha256(
        json.dumps(["q", v], sort_keys=True, ensure_ascii=False).encode()).hexdigest() + ":lock", "1", ex=1)
    sleeps = []
    def fake_sleep(s):
        sleeps.append(s)
        fake_redis.delete(*[k for k in fake_redis.keys("*:lock")]) if len(sleeps) > 2 else None
    monkeypatch.setattr("goszakup.api.client.time.sleep", fake_sleep)
    follower.session.request = MagicMock(return_value=_gql_page([{"id": 2}], last_id=2, has_next=False))
    data, _ = follower.graphql("q", v, cache_ttl=900)
    assert data == {"TrdBuy": [{"id": 2}]}
    assert follower.session.request.call_count == 1


def test_graphql_errors_raise():
    c = _client()
    c.session.request = MagicMock(
        return_value=_resp(payload={"errors": [{"message": "Cannot query field X"}]})
    )
    with pytest.raises(OwsApiError, match="Cannot query field X"):
        c.graphql("{X}")
