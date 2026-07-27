"""Источник данных: официальный API OWS с фолбэком на HTML-скрейпинг.

Пайплайн (jobs/run_preset, queue/actors, web) работает с абстракцией
DataSource и не знает, откуда пришли dataclasses. Выбор — по наличию
GZ_OWS_TOKEN (make_source): токен есть → API с прозрачным HTML-фолбэком,
нет → чистый HTML, поведение бит-в-бит прежнее.

Деградация API не молчит (правило #20): каждый фолбэк пишет WARNING и
выставляет Redis-флаг goszakup:api_degraded с TTL — его читает health-check.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Protocol

from .api.client import OwsApiError, OwsClient
from .api.mapping import detail_from_trd_buy, kato_in_region, listing_hit_from_lot
from .api.queries import DETAIL_QUERY, LISTING_QUERY
from .config import OWS_TOKEN
from .queue.rate_limit import make_http_session
from .scraper.announce import (
    AnnouncementDetail,
    fetch_announcement,
    fetch_lot_enstru_code,
)
from .scraper.documents import DownloadResult, download_document
from .scraper.modal_files import ModalFile, fetch_modal_files
from .scraper.search import ListingHit, SearchParams, iter_listing

log = logging.getLogger(__name__)

API_DEGRADED_KEY = "goszakup:api_degraded"
_DEGRADED_TTL = 6 * 3600


class DataSource(Protocol):
    def iter_listing(
        self, params: SearchParams, max_pages: int | None = None
    ) -> Iterator[ListingHit]: ...

    def fetch_announcement(self, anno_id: int) -> AnnouncementDetail: ...

    def fetch_modal_files(self, anno_id: int, file_type_id: int) -> list[ModalFile]: ...

    def fetch_enstru_code(self, trd_buy_id: int | str, lot_id: int) -> str | None: ...

    def download(
        self, anno_id: int, url: str, suggested_name: str | None = None
    ) -> DownloadResult: ...


class HtmlSource:
    """Обёртка над scraper/* — прежний путь через ThrottledSession."""

    def __init__(self, http=None) -> None:
        self.http = http or make_http_session()

    def iter_listing(self, params, max_pages=None):
        yield from iter_listing(params, session=self.http, max_pages=max_pages)

    def fetch_announcement(self, anno_id):
        return fetch_announcement(anno_id, session=self.http)

    def fetch_modal_files(self, anno_id, file_type_id):
        return fetch_modal_files(anno_id, file_type_id, session=self.http)

    def fetch_enstru_code(self, trd_buy_id, lot_id):
        return fetch_lot_enstru_code(trd_buy_id, lot_id, session=self.http)

    def download(self, anno_id, url, suggested_name=None):
        return download_document(anno_id, url, session=self.http, suggested_name=suggested_name)


class ApiSource:
    """Источник поверх OWS: листинг и деталь — GraphQL, файлы — download с Bearer."""

    def __init__(self, client: OwsClient | None = None, *, redis_client=None) -> None:
        self.client = client or OwsClient(redis_client=redis_client)

    def iter_listing(self, params: SearchParams, max_pages=None):
        f: dict = {}
        if params.status_codes:
            f["refLotStatusId"] = list(params.status_codes)
        if params.amount_from:
            # amount: [X] означает «от X» (NOTES.md); amount_to добавляет потолок.
            f["amount"] = (
                [params.amount_from, params.amount_to]
                if params.amount_to
                else [params.amount_from]
            )
        if params.customer_bin:
            f["customerBin"] = params.customer_bin
        # Регион, год и вид предмета закупок на сервере не фильтруются
        # (kato — только точные коды) — отсеиваем клиентски ниже.
        seen: set[int] = set()
        # Кэш страниц на 15 мин: региональные preset'ы отличаются только kato
        # (клиентский фильтр), их листинг-страницы идентичны — при daily 20
        # акторов проходят по одному и тому же кэшу вместо 20 обходов API.
        for lot in self.client.iter_graphql(
            LISTING_QUERY, {"f": f}, root="Lots", limit=200, max_pages=max_pages,
            cache_ttl=900,
        ):
            if lot["id"] in seen:
                continue
            seen.add(lot["id"])
            tb = lot.get("TrdBuy") or {}
            if params.kato:
                # У ~9% лотов plnPointKatoList в индексе пуст — тогда (и только
                # тогда) берём КАТО объявления. Всегда объединять нельзя: у
                # мультирегионального объявления лоты чужих регионов начали бы
                # матчиться на каждый регион из списка.
                kato_list = [k for k in lot.get("plnPointKatoList") or [] if k]
                if not kato_list:
                    kato_list = tb.get("kato") or []
                if not kato_in_region(kato_list, params.kato):
                    continue
            if params.year:
                pub = tb.get("publishDate") or ""
                if not pub.startswith(str(params.year)):
                    continue
            if params.trade_type:
                # refSubjectTypeId: 1=Товар(g), 2=Работа(r), 3=Услуга(s) — как
                # на форме поиска.
                subj = {"g": 1, "r": 2, "s": 3}.get(params.trade_type)
                if subj and tb.get("refSubjectTypeId") != subj:
                    continue
            if params.enstru:
                # На форме filter[enstru] принимает код и префикс кода; в API
                # кода в листинге может не быть — сверяем по нему, когда есть.
                codes = [
                    (plan.get("RefEnstru") or {}).get("code")
                    for plan in lot.get("Plans") or []
                ]
                codes = [c for c in codes if c]
                if codes and not any(c.startswith(params.enstru) for c in codes):
                    continue
            yield listing_hit_from_lot(lot)

    def fetch_announcement(self, anno_id: int) -> AnnouncementDetail:
        data, _ = self.client.graphql(DETAIL_QUERY, {"f": {"id": int(anno_id)}})
        items = data.get("TrdBuy") or []
        if not items:
            raise OwsApiError(f"OWS: объявление {anno_id} не найдено")
        return detail_from_trd_buy(items[0])

    def fetch_modal_files(self, anno_id, file_type_id):
        # В API-выдаче файлы приходят прямыми ссылками (Files.filePath),
        # модального шага нет — сюда попадать нечему.
        return []

    def fetch_enstru_code(self, trd_buy_id, lot_id):
        # Код приходит в LotDetail.enstru_code из DETAIL_QUERY; отдельного
        # запроса, как у subpriceoffer, в API-пути нет.
        return None

    def download(self, anno_id, url, suggested_name=None):
        return download_document(
            anno_id, url, session=self.client, suggested_name=suggested_name
        )


class FallbackSource:
    """API как основной путь, HTML — страховка на OwsApiError."""

    def __init__(self, api: ApiSource, html: HtmlSource, *, redis_client=None) -> None:
        self.api = api
        self.html = html
        self.redis = redis_client

    def _degraded(self, op: str, exc: Exception) -> None:
        log.warning("OWS %s: %s — фолбэк на HTML-скрейпинг", op, exc)
        if self.redis is not None:
            try:
                self.redis.set(API_DEGRADED_KEY, f"{op}: {exc}", ex=_DEGRADED_TTL)
            except Exception:  # noqa: BLE001
                pass

    def iter_listing(self, params, max_pages=None):
        try:
            yield from self.api.iter_listing(params, max_pages=max_pages)
        except OwsApiError as e:
            self._degraded("listing", e)
            yield from self.html.iter_listing(params, max_pages=max_pages)

    def fetch_announcement(self, anno_id):
        try:
            return self.api.fetch_announcement(anno_id)
        except OwsApiError as e:
            self._degraded(f"detail {anno_id}", e)
            return self.html.fetch_announcement(anno_id)

    def fetch_modal_files(self, anno_id, file_type_id):
        # Модальный шаг существует только в HTML-детали; после API-детали
        # file_type_id всегда None и вызова не будет.
        return self.html.fetch_modal_files(anno_id, file_type_id)

    def fetch_enstru_code(self, trd_buy_id, lot_id):
        # API-деталь уже принесла код в LotDetail; если его не было (план не
        # индексирован) — добираем прежним HTML-запросом subpriceoffer.
        return self.html.fetch_enstru_code(trd_buy_id, lot_id)

    def download(self, anno_id, url, suggested_name=None):
        # Маршрут по URL: ows-ссылки (API-деталь) требуют Bearer, ссылки на
        # goszakup/v3bl (HTML-деталь после фолбэка) — HTML-сессию с туннелем.
        if "ows.goszakup" in url:
            return self.api.download(anno_id, url, suggested_name=suggested_name)
        return self.html.download(anno_id, url, suggested_name=suggested_name)


def make_source(redis_client=None) -> DataSource:
    html = HtmlSource(make_http_session(redis_client))
    if not OWS_TOKEN:
        return html
    api = ApiSource(redis_client=redis_client)
    return FallbackSource(api, html, redis_client=redis_client)
