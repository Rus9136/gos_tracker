"""Прогресс прогона для индикатора дозагрузки (jobs.ingest.run_progress).

Знаменатель есть только у фазы деталей: обход выдачи идёт до первой пустой
страницы, сколько всего объявлений — заранее неизвестно. Проценты берутся от
pending-счётчика (сколько ОСТАЛОСЬ), а не от details_fetched: тот растёт и от
ретраев, знаменатель по нему не восстановить.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from goszakup.db.models import ScrapeRun
from goszakup.jobs.ingest import run_progress


class FakeRedis:
    def __init__(self, **values):
        self.values = values

    def get(self, key):
        return self.values.get(key.rsplit(":", 1)[-1])


class DeadRedis:
    def get(self, key):
        raise ConnectionError("redis лёг")


def _run(**kw) -> ScrapeRun:
    return ScrapeRun(id=7, preset_id=None, listing_count=0, **kw)


def test_finished_run_is_hundred_percent():
    p = run_progress(_run(finished_at=datetime.now(UTC)), FakeRedis())
    assert p == {"finished": True, "phase": "done", "percent": 100}


def test_listing_phase_has_no_denominator():
    # Счётчиков ещё нет — фаза обхода выдачи, показываем только «просмотрено».
    run = _run()
    run.listing_count = 120
    p = run_progress(run, FakeRedis())
    assert p["phase"] == "listing" and p["listing_count"] == 120
    assert "percent" not in p


def test_details_phase_counts_from_pending():
    p = run_progress(_run(), FakeRedis(pending="30", total="100"))
    assert p["phase"] == "details"
    assert (p["done"], p["total"], p["percent"]) == (70, 100, 70)
    assert p["eta_seconds"] is None  # без метки старта темп не посчитать


def test_eta_extrapolates_from_rate():
    # 60 из 100 за 60с → ~1с на объявление → на оставшиеся 40 ≈40с.
    started = int(time.time()) - 60
    p = run_progress(_run(), FakeRedis(pending="40", total="100", details_started=str(started)))
    assert 35 <= p["eta_seconds"] <= 45


def test_eta_waits_for_a_few_items():
    # На первых двух деталях темп не показателен (прогрев соединения, кеш
    # справочников) — врать пользователю минутами не хотим.
    started = int(time.time()) - 30
    p = run_progress(_run(), FakeRedis(pending="98", total="100", details_started=str(started)))
    assert p["eta_seconds"] is None


def test_dead_redis_degrades_to_listing():
    # Индикатор — украшение: падение Redis не должно ронять страницу отчёта.
    assert run_progress(_run(), DeadRedis())["phase"] == "listing"
    assert run_progress(_run(), None)["phase"] == "listing"


def test_overshot_counter_does_not_go_negative():
    # Поздний ретрай detail'а может увести счётчик в минус — процент обязан
    # остаться в границах.
    p = run_progress(_run(), FakeRedis(pending="-3", total="10"))
    assert (p["done"], p["percent"]) == (10, 100)
