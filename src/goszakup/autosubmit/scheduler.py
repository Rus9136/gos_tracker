"""Диспетчер автоподачи: ставить агенту задачи к `open_at` и применять результат.

Пред-стейдж невозможен, поэтому агенту надо успеть ПРОГРЕТЬСЯ (логин, страница
объявления, разблокировка PIN) до открытия — значит задачу шлём заранее, за
`warmup_lead` секунд до `open_at`. Сам момент выстрела держит агент (он на
Windows с реальным Tumar). Linux лишь раздаёт задачи и принимает отчёты.

`dispatch_due_submissions` идемпотентен по статусу: берёт только `PLANNED`,
переводит в `ARMED` после успешной отправки. На ошибке оставляет `PLANNED`
(следующий тик повторит), считая попытки — после лимита `FAILED`.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Submission
from ..vault.credentials import decrypt_credential
from ..vault.crypto import decrypt_str
from .agent_client import AgentClient, AgentError
from .rpc import LotBid, RunRequest, RunResult

log = logging.getLogger(__name__)

MAX_DISPATCH_ATTEMPTS = 5


def _decrypt_bids(sub: Submission) -> list[LotBid]:
    """bid_enc/bid_nonce → [{lot_id, price}] → LotBid. Цена в plaintext только тут."""
    raw = decrypt_str(sub.bid_enc, sub.bid_nonce)
    return [LotBid(lot_id=int(b["lot_id"]), price=str(b["price"])) for b in json.loads(raw)]


def build_run_request(sub: Submission, *, clock_offset: float = 0.0) -> RunRequest:
    cred = decrypt_credential(sub.credential)
    return RunRequest(
        submission_id=sub.id,
        anno_id=sub.anno_id,
        anno_number=sub.anno_number,
        open_at_iso=sub.open_at.isoformat(),
        close_at_iso=sub.close_at.isoformat() if sub.close_at else None,
        lot_bids=_decrypt_bids(sub),
        p12_b64=base64.b64encode(cred.p12_bytes).decode(),
        portal_password=cred.portal_password,
        key_pin=cred.key_pin,
        clock_offset=clock_offset,
    )


def dispatch_due_submissions(
    session: Session,
    agent: AgentClient,
    *,
    now: datetime | None = None,
    warmup_lead: int = 300,
    agent_node: str | None = None,
) -> list[int]:
    """Отправить агенту PLANNED-подачи, открывающиеся в ближайшие `warmup_lead` с.

    Возвращает id успешно отправленных (переведённых в ARMED).
    """
    now = now or datetime.now(UTC)
    horizon = now + timedelta(seconds=warmup_lead)
    rows = session.scalars(
        select(Submission).where(
            Submission.status == "PLANNED",
            Submission.open_at <= horizon,
        )
    ).all()

    armed: list[int] = []
    for sub in rows:
        sub.attempts += 1
        try:
            agent.dispatch(build_run_request(sub))
        except AgentError as e:
            log.warning("autosubmit dispatch #%s failed (attempt %s): %s", sub.id, sub.attempts, e)
            sub.error = str(e)
            if sub.attempts >= MAX_DISPATCH_ATTEMPTS:
                sub.status = "FAILED"
            continue
        sub.status = "ARMED"
        sub.armed_at = now
        sub.agent_node = agent_node
        sub.error = None
        armed.append(sub.id)

    session.commit()
    if armed:
        log.info("autosubmit armed: %s", armed)
    return armed


def apply_result(session: Session, result: RunResult) -> Submission | None:
    """Применить отчёт агента к Submission (ingest финального RunResult)."""
    sub = session.get(Submission, result.submission_id)
    if sub is None:
        log.warning("autosubmit result для несуществующей submission #%s", result.submission_id)
        return None

    sub.status = result.status
    if result.app_id is not None:
        sub.app_id = result.app_id
    if result.fire_latency_ms is not None:
        sub.fire_latency_ms = result.fire_latency_ms
    if result.fired_at_iso:
        sub.fired_at = datetime.fromisoformat(result.fired_at_iso)
    if result.submitted_at_iso:
        sub.submitted_at = datetime.fromisoformat(result.submitted_at_iso)
        if result.status in ("SUBMITTED", "CONFIRMED"):
            sub.confirmed_at = sub.confirmed_at or sub.submitted_at
    sub.result = result.result
    sub.error = result.error
    session.commit()
    log.info("autosubmit result #%s → %s (latency=%sms)", sub.id, sub.status, sub.fire_latency_ms)
    return sub
