"""Выбор источника по GZ_OWS_TOKEN и поведение FallbackSource."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis
import pytest

from goszakup.api.client import OwsApiError
from goszakup.scraper.search import SearchParams
from goszakup.sources import (
    API_DEGRADED_KEY,
    ApiSource,
    FallbackSource,
    HtmlSource,
    make_source,
)


def test_make_source_without_token(monkeypatch):
    monkeypatch.setattr("goszakup.sources.OWS_TOKEN", None)
    assert isinstance(make_source(), HtmlSource)


def test_make_source_with_token(monkeypatch):
    monkeypatch.setattr("goszakup.sources.OWS_TOKEN", "t")
    monkeypatch.setattr("goszakup.api.client.OWS_TOKEN", "t")
    assert isinstance(make_source(), FallbackSource)


@pytest.fixture
def fallback():
    api = MagicMock(spec=ApiSource)
    html = MagicMock(spec=HtmlSource)
    r = fakeredis.FakeStrictRedis(decode_responses=True)
    return FallbackSource(api, html, redis_client=r), api, html, r


def test_fallback_detail_on_api_error(fallback):
    src, api, html, r = fallback
    api.fetch_announcement.side_effect = OwsApiError("boom")
    html.fetch_announcement.return_value = "html-detail"
    assert src.fetch_announcement(123) == "html-detail"
    html.fetch_announcement.assert_called_once_with(123)
    # Одиночный промах фолбэк отрабатывает, но деградацией не объявляет:
    # по флагу detail_actor сужается до watchlist на весь рынок.
    assert not r.exists(API_DEGRADED_KEY)


def test_degraded_flag_after_threshold(fallback):
    src, api, html, r = fallback
    api.fetch_announcement.side_effect = OwsApiError("boom")
    html.fetch_announcement.return_value = "html-detail"
    for _ in range(3):
        src.fetch_announcement(123)
    assert r.exists(API_DEGRADED_KEY)
    assert "boom" in r.get(API_DEGRADED_KEY)


def test_no_fallback_when_api_ok(fallback):
    src, api, html, r = fallback
    api.fetch_announcement.return_value = "api-detail"
    assert src.fetch_announcement(123) == "api-detail"
    html.fetch_announcement.assert_not_called()
    assert not r.exists(API_DEGRADED_KEY)


def test_fallback_listing_on_api_error(fallback):
    src, api, html, r = fallback

    def boom(params, max_pages=None):
        raise OwsApiError("listing down")
        yield  # noqa: unreachable — генератор

    api.iter_listing.side_effect = boom
    html.iter_listing.return_value = iter(["hit1", "hit2"])
    assert list(src.iter_listing(SearchParams())) == ["hit1", "hit2"]


def test_download_routes_by_url(fallback):
    src, api, html, r = fallback
    src.download(1, "https://ows.goszakup.gov.kz/download/trd_buy/abc")
    api.download.assert_called_once()
    html.download.assert_not_called()
    src.download(1, "https://v3bl.goszakup.gov.kz//uploads/x.pdf")
    html.download.assert_called_once()
