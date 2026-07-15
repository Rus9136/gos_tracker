"""Оркестратор одной подачи: прогрев → ожидание open_at → визард → отчёт.

Любое исключение превращается в RunResult(status=FAILED) и всё равно
отправляется на Linux — Linux не должен «вечно ждать» ARMED-подачу.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .protocol import RunRequest, RunResult
from .report import report_result
from .timing import deadline_guard, wait_until
from .wizard import Wizard

log = logging.getLogger(__name__)


def run(req: RunRequest) -> RunResult:
    log.info("run #%s: anno=%s open_at=%s", req.submission_id, req.anno_id, req.open_at_iso)
    wiz = Wizard(req, on_status=lambda s, d: log.info("#%s [%s] %s", req.submission_id, s, d))
    try:
        wiz.start()
        wiz.warm_up()  # логин + страница объявления (до open_at)
        open_at = datetime.fromisoformat(req.open_at_iso)
        log.info("#%s прогрет, ждём open_at", req.submission_id)
        wait_until(open_at, clock_offset=req.clock_offset, lead=req.fire_lead)
        close_at = (
            datetime.fromisoformat(req.close_at_iso) if req.close_at_iso else None
        )
        if not deadline_guard(close_at):
            # Дедлайн подачи истёк, пока ждали/прогревались — не стреляем.
            log.warning("run #%s: close_at прошёл — подача пропущена", req.submission_id)
            result = RunResult(
                submission_id=req.submission_id,
                status="SKIPPED",
                error="close_at прошёл до выстрела",
            )
        else:
            fields = wiz.submit_after_open()  # гонка визарда
            result = RunResult(submission_id=req.submission_id, **fields)
    except Exception as e:  # noqa: BLE001 — любой сбой → FAILED + отчёт
        log.exception("run #%s failed", req.submission_id)
        result = RunResult(
            submission_id=req.submission_id,
            status="FAILED",
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        wiz.close()

    report_result(result)
    return result
