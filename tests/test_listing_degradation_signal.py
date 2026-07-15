"""Гейт 2 (P0 №7): смена вёрстки листинга даёт активный сигнал, а не тихий ноль.

Позиционный парс (tds[0..6]) при смене вёрстки goszakup вернул бы 0 строк,
пагинация остановилась бы, прогон «успешно» завершился бы с нулём. Теперь строки,
которые ссылаются на объявление, но не распарсились, поднимают WARNING.
"""

from __future__ import annotations

import logging

from goszakup.scraper.search import SearchParams, collect_listing


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, *pages):
        self.pages = pages
        self.i = 0

    def get(self, url, params=None):
        page = self.pages[min(self.i, len(self.pages) - 1)]
        self.i += 1
        return _Resp(page)


_PARAMS = SearchParams(kato="750000000", amount_from=0)

# Строка ЯВНО про лот (ссылка на объявление), но всего 2 <td> — вёрстка «поехала».
_DEGRADED = """
<html><body>
<span>4 записей</span>
<table><tbody>
<tr><td>1</td><td><a href="/ru/announce/index/123">123 Некий лот</a></td></tr>
</tbody></table>
</body></html>
"""

# Легитимно пустая выдача: ни строк-лотов, ни счётчика записей.
_EMPTY = "<html><body><table><tbody></tbody></table></body></html>"


def test_degraded_layout_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="goszakup.scraper.search"):
        hits = collect_listing(_PARAMS, session=_FakeSession(_DEGRADED))
    assert hits == []
    msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("не распарсилась" in m or "деградировал" in m for m in msgs)


def test_legit_empty_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="goszakup.scraper.search"):
        hits = collect_listing(_PARAMS, session=_FakeSession(_EMPTY))
    assert hits == []
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
