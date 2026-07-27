"""daily_actor: ветвление API-инкремент vs preset-обход по GZ_OWS_TOKEN."""

from __future__ import annotations

from unittest.mock import MagicMock

from goszakup.queue import actors


def test_daily_with_token_uses_incremental(monkeypatch):
    monkeypatch.setattr(actors, "OWS_TOKEN", "t")
    api, contracts, listing, expire = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(actors.api_daily_actor, "send", api)
    monkeypatch.setattr(actors.contracts_sync_actor, "send", contracts)
    monkeypatch.setattr(actors.listing_actor, "send", listing)
    monkeypatch.setattr(actors.expire_actor, "send", expire)
    actors.daily_actor.fn()
    api.assert_called_once()
    contracts.assert_called_once()
    listing.assert_not_called()
    expire.assert_called_once()


def test_daily_without_token_uses_presets(monkeypatch):
    monkeypatch.setattr(actors, "OWS_TOKEN", None)
    api, contracts, listing, expire = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(actors.api_daily_actor, "send", api)
    monkeypatch.setattr(actors.contracts_sync_actor, "send", contracts)
    monkeypatch.setattr(actors.listing_actor, "send", listing)
    monkeypatch.setattr(actors.expire_actor, "send", expire)
    actors.daily_actor.fn()
    api.assert_not_called()
    contracts.assert_not_called()
    expire.assert_called_once()
    # listing_actor дёргается по активным preset'ам (сколько их есть в тест-БД).


def test_new_actors_registration():
    assert actors.api_daily_actor.queue_name == "goszakup_daily"
    assert actors.contracts_sync_actor.queue_name == "goszakup_daily"
    assert actors.api_daily_actor.options.get("max_retries") == 0
    assert actors.contracts_sync_actor.options.get("max_retries") == 0
