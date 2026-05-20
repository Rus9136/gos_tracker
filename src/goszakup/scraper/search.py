"""Universal listing scraper для /ru/search/lots.

Возвращает генератор минимальных «hits»: lot_id, announcement_id и поля,
доступные в табличном выводе. Полные детали тянутся отдельным шагом
через scraper.announce.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html import unescape
from typing import Iterable, Iterator

from bs4 import BeautifulSoup

from ..config import MIN_AMOUNT, SEARCH_URL
from .http import ThrottledSession

log = logging.getLogger(__name__)

PER_PAGE = 50


@dataclass
class ListingHit:
    lot_id: int
    lot_number: str
    announcement_id: int
    announcement_number: str
    announcement_url: str
    lot_name: str
    customer_name: str
    enstru: str
    quantity: str
    plan_amount: float | None
    amount_raw: str
    method: str
    status_name: str
    trd_buy_id: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SearchParams:
    kato: str = ""
    amount_from: int = MIN_AMOUNT
    amount_to: int | None = None
    status_codes: list[int] = field(default_factory=list)
    year: int | None = None
    # БИН заказчика (filter[customer] на форме). Когда задан — поиск
    # ограничивается одной организацией; обычно используется с пустым kato.
    customer_bin: str = ""
    # Вид предмета закупок (filter[trade_type]): g=Товар, s=Услуга, r=Работа.
    # Пустая строка — не ограничивать.
    trade_type: str = ""
    per_page: int = PER_PAGE

    def to_query(self, page: int) -> dict:
        q: dict = {
            "filter[amount_from]": self.amount_from,
            "count_record": self.per_page,
            "page": page,
        }
        if self.kato:
            q["filter[kato]"] = self.kato
        if self.amount_to:
            q["filter[amount_to]"] = self.amount_to
        if self.year:
            q["filter[year]"] = self.year
        if self.status_codes:
            q["filter[status][]"] = [str(s) for s in self.status_codes]
        if self.customer_bin:
            q["filter[customer]"] = self.customer_bin
        if self.trade_type:
            q["filter[trade_type]"] = self.trade_type
        return q


def _clean(s: str) -> str:
    return unescape(re.sub(r"\s+", " ", s)).strip()


def _parse_amount(s: str) -> float | None:
    if not s:
        return None
    s = s.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_row(tr) -> ListingHit | None:
    tds = tr.find_all("td", recursive=False)
    if len(tds) < 7 or not tr.find("a", href=re.compile(r"/ru/announce/index/")):
        return None

    lot_number = _clean(tds[0].get_text(" "))

    anno_link = tds[1].find("a", href=re.compile(r"/ru/announce/index/"))
    anno_href = anno_link["href"]
    anno_id_m = re.search(r"/index/(\d+)", anno_href)
    if not anno_id_m:
        return None
    anno_id = int(anno_id_m.group(1))

    name_strong = _clean(anno_link.get_text(" "))
    m = re.match(r"^([\d\-]+)\s+(.*)$", name_strong)
    anno_number = m.group(1) if m else ""
    lot_name = m.group(2) if m else name_strong

    customer = ""
    cust_label = tds[1].find("b", string=re.compile(r"Заказчик"))
    if cust_label and cust_label.parent:
        customer = (
            _clean(cust_label.parent.get_text(" "))
            .replace("Заказчик:", "")
            .strip()
        )

    enstru = ""
    enstru_link = tds[2].find("a")
    if enstru_link:
        enstru = _clean(enstru_link.get_text(" "))

    history_btn = tds[2].find(attrs={"data-lot-id": True})
    lot_id = int(history_btn["data-lot-id"]) if history_btn else 0
    trd_buy_id = history_btn["data-trb-buy"] if history_btn else str(anno_id)
    if not lot_id:
        return None

    quantity = _clean(tds[3].get_text(" "))
    amount_raw = _clean(tds[4].get_text(" "))
    method = _clean(tds[5].get_text(" "))
    status = _clean(tds[6].get_text(" "))

    return ListingHit(
        lot_id=lot_id,
        lot_number=lot_number,
        announcement_id=anno_id,
        announcement_number=anno_number,
        announcement_url=f"https://goszakup.gov.kz{anno_href}",
        lot_name=lot_name,
        customer_name=customer,
        enstru=enstru,
        quantity=quantity,
        plan_amount=_parse_amount(amount_raw),
        amount_raw=amount_raw,
        method=method,
        status_name=status,
        trd_buy_id=trd_buy_id,
    )


def iter_listing(
    params: SearchParams,
    session: ThrottledSession | None = None,
    max_pages: int | None = None,
) -> Iterator[ListingHit]:
    sess = session or ThrottledSession()
    seen: set[int] = set()
    page = 1
    total: int | None = None
    while True:
        if max_pages and page > max_pages:
            return
        resp = sess.get(SEARCH_URL, params=params.to_query(page))
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        if total is None:
            m = re.search(r"(\d[\d\s]*)\s*записей", resp.text)
            if m:
                total = int(re.sub(r"\s", "", m.group(1)))
                log.info("listing total: %s", total)

        new_in_page = 0
        for tr in soup.find_all("tr"):
            hit = _parse_row(tr)
            if hit is None or hit.lot_id in seen:
                continue
            seen.add(hit.lot_id)
            new_in_page += 1
            yield hit

        log.info("page %d → +%d (всего %d/%s)", page, new_in_page, len(seen), total or "?")
        if new_in_page == 0:
            return
        if total is not None and len(seen) >= total:
            return
        page += 1


def collect_listing(params: SearchParams, **kw) -> list[ListingHit]:
    return list(iter_listing(params, **kw))
