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


def test_graphql_errors_raise():
    c = _client()
    c.session.request = MagicMock(
        return_value=_resp(payload={"errors": [{"message": "Cannot query field X"}]})
    )
    with pytest.raises(OwsApiError, match="Cannot query field X"):
        c.graphql("{X}")
