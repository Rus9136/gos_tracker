"""Отчёт о результате обратно на Linux (`POST /autosubmit/result`).

Defensive: сетевая ошибка отчёта не должна ронять процесс — логируем и выходим
(Linux всё равно увидит «зависшую» ARMED-подачу по таймауту и разберётся).
"""

from __future__ import annotations

import logging

import httpx

from . import config
from .protocol import RunResult

log = logging.getLogger(__name__)


def report_result(result: RunResult) -> bool:
    if not config.LINUX_INGEST_URL:
        log.warning("report: GZ_LINUX_INGEST_URL не задан — отчёт #%s некуда слать", result.submission_id)
        return False
    headers = {}
    if config.INGEST_TOKEN:
        headers["X-Autosubmit-Token"] = config.INGEST_TOKEN
    try:
        resp = httpx.post(config.LINUX_INGEST_URL, json=result.to_dict(), headers=headers, timeout=15.0)
        resp.raise_for_status()
        log.info("report #%s → %s ok", result.submission_id, result.status)
        return True
    except httpx.HTTPError as e:
        log.error("report #%s failed: %s", result.submission_id, e)
        return False
