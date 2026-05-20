"""Структурные проверки actor'ов — без реального broker'а.

Полный end-to-end actor-run требует живой Postgres + Redis + worker. Это —
отдельный интеграционный тест, который запускается через docker compose.
Здесь же убеждаемся, что actor'ы корректно зарегистрированы и сигнатуры
не сломаются на ровном месте.
"""

from __future__ import annotations

from goszakup.queue.actors import (
    analyze_actor,
    daily_actor,
    detail_actor,
    ingest_actor,
    listing_actor,
    scan_actor,
)


def test_actors_registered_with_correct_queue_names():
    # Очереди named так, что worker может подписываться выборочно.
    assert daily_actor.queue_name == "goszakup_daily"
    assert listing_actor.queue_name == "goszakup_listing"
    assert detail_actor.queue_name == "goszakup_detail"
    assert analyze_actor.queue_name == "goszakup_llm"
    # ingest и scan семантически listing — кладём в ту же очередь.
    assert ingest_actor.queue_name == "goszakup_listing"
    assert scan_actor.queue_name == "goszakup_listing"


def test_retry_policy():
    # daily не ретраим — он тривиален, ошибка скорее в БД, чем в логике.
    assert daily_actor.options.get("max_retries") == 0
    # listing/detail/llm ретраим по 2-3 раза с экспоненциальным бэкоффом.
    assert listing_actor.options.get("max_retries") == 2
    assert detail_actor.options.get("max_retries") == 3
    assert analyze_actor.options.get("max_retries") == 2
    assert scan_actor.options.get("max_retries") == 2


def test_time_limits_set():
    # Каждый actor имеет time_limit — чтобы зависший worker не держал
    # сообщение вечно.
    for a in (listing_actor, detail_actor, analyze_actor, ingest_actor, scan_actor):
        assert a.options.get("time_limit"), f"{a.actor_name} без time_limit"
