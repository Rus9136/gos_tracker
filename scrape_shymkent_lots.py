"""
Скрапер реестра лотов goszakup.gov.kz.
По умолчанию: г. Шымкент (КАТО 790000000), сумма от 100 000 000 тг.
Соблюдает Crawl-delay: 5s из robots.txt.

Поддерживает фильтры формы /ru/search/lots:
  filter[kato], filter[customer] (БИН), filter[trade_type]
  (g=Товар, s=Услуга, r=Работа), filter[amount_from/to],
  filter[status][], filter[year].
"""

import argparse
import csv
import re
import sys
import time
from html import unescape

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://goszakup.gov.kz/ru/search/lots"
CRAWL_DELAY = 5.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.8",
}

# КАТО Шымкента: 790000000 — весь город;
# районы: 791110000 Абайский, 791310000 Аль-Фараби,
# 791510000 Енбекшинский, 791710000 Каратауский, 791910000 Тұран.
DEFAULT_KATO = "790000000"
DEFAULT_AMOUNT_FROM = 100_000_000
PER_PAGE = 50


def clean_text(s: str) -> str:
    return unescape(re.sub(r"\s+", " ", s)).strip()


def parse_amount(s: str) -> float | None:
    if not s:
        return None
    s = s.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_row(tr) -> dict | None:
    tds = tr.find_all("td", recursive=False)
    if len(tds) < 7 or not tr.find("a", href=re.compile(r"/ru/announce/index/")):
        return None

    lot_number = clean_text(tds[0].get_text(" "))

    anno_link = tds[1].find("a", href=re.compile(r"/ru/announce/index/"))
    anno_href = anno_link["href"] if anno_link else ""
    anno_id_m = re.search(r"/index/(\d+)", anno_href)
    anno_id = anno_id_m.group(1) if anno_id_m else ""

    # 16983258-2 «Название» — отделяем номер от наименования
    name_strong = anno_link.get_text(" ") if anno_link else ""
    name_strong = clean_text(name_strong)
    m = re.match(r"^([\d\-]+)\s+(.*)$", name_strong)
    anno_number = m.group(1) if m else ""
    lot_name = m.group(2) if m else name_strong

    customer = ""
    cust_label = tds[1].find("b", string=re.compile(r"Заказчик"))
    if cust_label and cust_label.parent:
        customer = clean_text(cust_label.parent.get_text(" ")).replace("Заказчик:", "").strip()

    enstru = ""
    enstru_link = tds[2].find("a")
    if enstru_link:
        enstru = clean_text(enstru_link.get_text(" "))

    history_btn = tds[2].find(attrs={"data-lot-id": True})
    lot_id = history_btn["data-lot-id"] if history_btn else ""
    trd_buy_id = history_btn["data-trb-buy"] if history_btn else anno_id

    quantity = clean_text(tds[3].get_text(" "))
    amount_raw = clean_text(tds[4].get_text(" "))
    method = clean_text(tds[5].get_text(" "))
    status = clean_text(tds[6].get_text(" "))

    return {
        "lot_id": lot_id,
        "lot_number": lot_number,
        "trd_buy_id": trd_buy_id,
        "announcement_number": anno_number,
        "lot_name": lot_name,
        "customer": customer,
        "enstru": enstru,
        "quantity": quantity,
        "amount": parse_amount(amount_raw),
        "amount_raw": amount_raw,
        "method": method,
        "status": status,
        "announcement_url": f"https://goszakup.gov.kz{anno_href}" if anno_href else "",
    }


def fetch_page(session: requests.Session, page: int, params: dict) -> tuple[list[dict], int]:
    q = {**params, "page": page}
    resp = session.get(BASE_URL, params=q, timeout=30)
    resp.raise_for_status()
    # lxml корректно закрывает незакрытые </td> на этом сайте.
    soup = BeautifulSoup(resp.text, "lxml")

    rows = []
    for tr in soup.find_all("tr"):
        row = parse_row(tr)
        if row:
            rows.append(row)

    total = 0
    m = re.search(r"(\d[\d\s]*)\s*записей", resp.text)
    if m:
        total = int(re.sub(r"\s", "", m.group(1)))

    return rows, total


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kato", default=DEFAULT_KATO, help="КАТО (по умолчанию 790000000 — Шымкент; '' чтобы не ограничивать)")
    ap.add_argument("--customer-bin", default="", help="БИН заказчика (filter[customer])")
    ap.add_argument("--trade-type", default="", choices=["", "g", "s", "r"],
                    help="Вид предмета закупок: g=Товар, s=Услуга, r=Работа")
    ap.add_argument("--amount-from", type=int, default=DEFAULT_AMOUNT_FROM)
    ap.add_argument("--amount-to", type=int, default=None)
    ap.add_argument("--status", action="append", default=[], help="filter[status][] (можно повторять)")
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--output", default="shymkent_lots.csv")
    ap.add_argument("--delay", type=float, default=CRAWL_DELAY)
    ap.add_argument("--max-pages", type=int, default=None, help="ограничить число страниц (для теста)")
    args = ap.parse_args()

    params = {
        "filter[amount_from]": args.amount_from,
        "count_record": PER_PAGE,
    }
    if args.kato:
        params["filter[kato]"] = args.kato
    if args.customer_bin:
        params["filter[customer]"] = args.customer_bin
    if args.trade_type:
        params["filter[trade_type]"] = args.trade_type
    if args.amount_to:
        params["filter[amount_to]"] = args.amount_to
    if args.year:
        params["filter[year]"] = args.year
    for st in args.status:
        params.setdefault("filter[status][]", []).append(st)

    session = requests.Session()
    session.headers.update(HEADERS)

    fields = [
        "lot_id", "lot_number", "trd_buy_id", "announcement_number",
        "lot_name", "customer", "enstru",
        "quantity", "amount", "amount_raw",
        "method", "status", "announcement_url",
    ]

    seen_ids: set[str] = set()
    page = 1
    written = 0

    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        while True:
            if args.max_pages and page > args.max_pages:
                break
            print(f"[page {page}] загружаем…", file=sys.stderr, flush=True)
            try:
                rows, total = fetch_page(session, page, params)
            except requests.RequestException as e:
                print(f"  ошибка: {e}; повтор через 10с", file=sys.stderr)
                time.sleep(10)
                continue

            if not rows:
                print("  пусто — выходим", file=sys.stderr)
                break

            new_rows = 0
            for r in rows:
                key = r["lot_id"] or f'{r["lot_number"]}|{r["announcement_number"]}'
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                writer.writerow(r)
                new_rows += 1
                written += 1

            print(
                f"  +{new_rows} (всего {written}/{total or '?'})",
                file=sys.stderr,
                flush=True,
            )

            if new_rows == 0:
                break
            if total and written >= total:
                break

            page += 1
            time.sleep(args.delay)

    print(f"\nГотово: {written} лотов → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
