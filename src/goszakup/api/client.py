"""Клиент официального API goszakup (OWS): Bearer, свой rate-limit, GraphQL.

Rate-limit независим от HTML-скрейпера: у того контракт robots.txt
(Crawl-delay 5с, ключ goszakup:rate_limit), у API заявленного лимита нет —
держим GZ_API_DELAY (дефолт 1с) на собственном ключе.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator

import requests

from ..config import API_DELAY, GZ_PROXY_URL, OWS_BASE_URL, OWS_TOKEN, OWS_USE_PROXY
from ..queue.rate_limit import LocalSlotLimiter, RedisSlotLimiter

log = logging.getLogger(__name__)

API_LIMIT_KEY = "goszakup:api_rate_limit"

# Ретраи на транзиентные ответы (перегрузка/прокси). 429 в бурст-тестах не
# наблюдался, но клиент обязан его пережить, если лимит всё-таки появится.
_RETRY_STATUSES = {429, 502, 503, 504}
_RETRY_DELAYS = (2, 5, 15)


class OwsApiError(RuntimeError):
    pass


class OwsAuthError(OwsApiError):
    """Токен не задан / истёк / невалиден.

    ows маскирует невалидный токен под 404 «Invalid Route» (не 401!) — без
    отдельного класса это неотличимо от опечатки в пути.
    """


class OwsClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        redis_client=None,
        delay: float = API_DELAY,
    ) -> None:
        self.token = token or OWS_TOKEN
        if not self.token:
            raise OwsAuthError("GZ_OWS_TOKEN не задан")
        self.base_url = (base_url or OWS_BASE_URL).rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        if OWS_USE_PROXY and GZ_PROXY_URL:
            self.session.proxies = {"http": GZ_PROXY_URL, "https": GZ_PROXY_URL}
        self._redis = redis_client
        if redis_client is not None:
            self._limiter = RedisSlotLimiter(redis_client, delay, API_LIMIT_KEY)
        else:
            self._limiter = LocalSlotLimiter(delay)

    # -- транспорт -----------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", 60)
        timeout = kwargs["timeout"]
        req_s = timeout if isinstance(timeout, (int, float)) else 60
        hold = int(self._limiter.delay + req_s + 5)
        last_exc: Exception | None = None
        for attempt, retry_delay in enumerate((*_RETRY_DELAYS, None)):
            self._limiter.acquire(hold)
            try:
                r = self.session.request(method, url, **kwargs)
            except requests.RequestException as e:
                last_exc = e
                r = None
            finally:
                self._limiter.release()
            if r is not None and r.status_code not in _RETRY_STATUSES:
                if r.status_code == 404 and "Invalid Route" in r.text[:500]:
                    raise OwsAuthError(
                        f"OWS ответил 404 «Invalid Route» на {url} — "
                        "токен истёк/невалиден или путь не существует"
                    )
                return r
            if retry_delay is None:
                break
            log.warning(
                "OWS %s %s: %s, ретрай через %sс (попытка %d)",
                method, url,
                f"HTTP {r.status_code}" if r is not None else last_exc,
                retry_delay, attempt + 1,
            )
            time.sleep(retry_delay)
        if last_exc is not None:
            raise OwsApiError(f"OWS {method} {url}: {last_exc}") from last_exc
        raise OwsApiError(f"OWS {method} {url}: HTTP {r.status_code} после ретраев")

    def get(self, url: str, **kwargs) -> requests.Response:
        """Совместим с download_document(session=...): .get(url, stream=..., ...).

        Маршрутизация (прямая или через туннель) определяется конфигом клиента —
        внешний kwarg proxies игнорируем, иначе _proxies_for() из documents.py
        загнал бы ows-хост в KZ-туннель по совпадению подстроки.
        """
        kwargs.pop("proxies", None)
        if url.startswith("/"):
            url = self.base_url + url
        return self._request("GET", url, **kwargs)

    def get_json(self, path: str, params: dict | None = None) -> dict:
        r = self.get(path, params=params)
        if r.status_code != 200:
            raise OwsApiError(f"OWS GET {path}: HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        # /v3/refs/* отвечает 200 с {"error": ...} на несуществующий справочник
        if isinstance(data, dict) and data.get("error"):
            raise OwsApiError(f"OWS GET {path}: {data['error']}")
        return data

    # -- GraphQL -------------------------------------------------------------

    def graphql(
        self, query: str, variables: dict | None = None, *, cache_ttl: int | None = None
    ) -> tuple[dict, dict]:
        """cache_ttl — короткий Redis-кэш ответа страницы.

        Нужен листингу daily: 20 preset'ов различаются только регионом,
        который фильтруется клиентски, поэтому все 20 акторов ходят по
        ИДЕНТИЧНОЙ последовательности страниц — без кэша это 20×74 запросов
        под общим rate-limit (акторы душат друг друга до TimeLimit),
        с кэшем — 74.
        """
        cache_key = None
        if cache_ttl and self._redis is not None:
            digest = hashlib.sha256(
                json.dumps([query, variables], sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            cache_key = f"goszakup:api:cache:{digest}"
            cached = self._cached_or_wait(cache_key)
            if cached is not None:
                return cached["data"], cached["pageInfo"]
        r = self._request(
            "POST",
            f"{self.base_url}/v3/graphql",
            json={"query": query, "variables": variables or {}},
        )
        if r.status_code != 200:
            raise OwsApiError(f"OWS graphql: HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        if payload.get("errors"):
            raise OwsApiError(f"OWS graphql: {payload['errors'][0].get('message')}")
        page_info = (payload.get("extensions") or {}).get("pageInfo") or {}
        data = payload.get("data") or {}
        if cache_key is not None:
            try:
                self._redis.set(
                    cache_key,
                    json.dumps({"data": data, "pageInfo": page_info}, ensure_ascii=False),
                    ex=cache_ttl,
                )
                self._redis.delete(cache_key + ":lock")
            except Exception:  # noqa: BLE001
                pass
        return data, page_info

    def _cached_or_wait(self, cache_key: str) -> dict | None:
        """Кэш-хит, либо ожидание страницы, которую уже качает другой актор.

        Конкурентные листинги идут по страницам в ногу (курсор N+1 известен
        только после страницы N): без fetch-лока все акторы промахиваются
        мимо кэша одновременно и дублируют запрос до 8×. Лидер берёт лок и
        качает, фолловеры ждут появления записи в кэше; не дождались (лидер
        умер, лок истёк по TTL) — качают сами.
        """
        try:
            cached = self._redis.get(cache_key)
            if cached:
                return json.loads(cached)
            if self._redis.set(cache_key + ":lock", "1", nx=True, ex=90):
                return None  # мы лидер — качаем сами
            for _ in range(120):
                time.sleep(0.5)
                cached = self._redis.get(cache_key)
                if cached:
                    return json.loads(cached)
                if self._redis.set(cache_key + ":lock", "1", nx=True, ex=90):
                    return None  # лок истёк — лидер умер, качаем сами
        except Exception:  # noqa: BLE001 — кэш best-effort, идём в API
            pass
        return None

    def iter_graphql(
        self,
        query: str,
        variables: dict | None = None,
        *,
        root: str,
        limit: int = 200,
        max_pages: int | None = None,
        cache_ttl: int | None = None,
    ) -> Iterator[dict]:
        """Курсорная пагинация: у query обязан быть аргумент `$after: Int`.

        Курсор — pageInfo.lastId предыдущей страницы; конец — hasNextPage=false
        или пустая страница (страховка от зацикливания на одном lastId).
        """
        variables = dict(variables or {})
        variables.setdefault("limit", limit)
        after: int | None = None
        pages = 0
        while True:
            variables["after"] = after
            data, page_info = self.graphql(query, variables, cache_ttl=cache_ttl)
            items = data.get(root) or []
            yield from items
            pages += 1
            last_id = page_info.get("lastId") or None
            if not items or not page_info.get("hasNextPage") or last_id == after:
                return
            if max_pages is not None and pages >= max_pages:
                return
            after = last_id
