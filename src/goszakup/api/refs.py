"""Кэш справочников OWS (/v3/refs/*) — имена способов закупки и статусов.

Справочники маленькие (десятки записей) и стабильные — грузим целиком один
раз на процесс. ЕНС ТРУ отдельного справочника в OWS нет («Справочник не
найден») — наименование позиции берём из Lots.nameRu (см. NOTES.md recon п.2).
"""

from __future__ import annotations

import logging

from .client import OwsApiError, OwsClient

log = logging.getLogger(__name__)

_cache: dict[str, dict[int, dict]] = {}


def ref_items(client: OwsClient, ref_name: str) -> dict[int, dict]:
    cached = _cache.get(ref_name)
    if cached is not None:
        return cached
    items: dict[int, dict] = {}
    path = f"/v3/refs/{ref_name}"
    while path:
        data = client.get_json(path, params={"limit": 200})
        for it in data.get("items") or []:
            items[int(it["id"])] = it
        path = data.get("next_page") or ""
    _cache[ref_name] = items
    return items


def trade_method_name(client: OwsClient, method_id: int | None) -> str:
    if not method_id:
        return ""
    try:
        item = ref_items(client, "ref_trade_methods").get(int(method_id))
    except OwsApiError as e:
        # Имя способа закупки — косметика, не роняем пайплайн из-за refs.
        log.warning("refs ref_trade_methods: %s", e)
        return ""
    return (item or {}).get("name_ru") or ""
