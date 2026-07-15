"""Dramatiq-слой автоподачи: периодический диспетчер задач submit-agent'у.

Очередь `goszakup_autosubmit`. Ставится таймером (как expire): найти PLANNED-
подачи, открывающиеся в ближайший warmup-lead, и разослать агенту на прогрев.
Реальную гонку держит Windows submit-agent.

ВАЖНО (CLAUDE.md, goszakup-worker.service): очередь `goszakup_autosubmit` должна
быть в `--queues` воркера — иначе задачи молча копятся в Redis.
"""

from __future__ import annotations

import logging

import dramatiq

from ..autosubmit.agent_client import AgentClient
from ..autosubmit.scheduler import dispatch_due_submissions
from ..config import (
    AUTOSUBMIT_AGENT_ALLOW_HTTP,
    AUTOSUBMIT_AGENT_TOKEN,
    AUTOSUBMIT_AGENT_URL,
    AUTOSUBMIT_WARMUP_LEAD,
)
from ..db.engine import SessionLocal
from .broker import broker  # noqa: F401 — импорт broker до actor'а обязателен

log = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="goszakup_autosubmit",
    max_retries=2,
    min_backoff=5_000,
    time_limit=120_000,
)
def autosubmit_dispatch_actor() -> None:
    if not AUTOSUBMIT_AGENT_URL:
        log.info("autosubmit: GZ_AUTOSUBMIT_AGENT_URL не задан — диспетчер выключен")
        return
    agent = AgentClient(
        AUTOSUBMIT_AGENT_URL,
        token=AUTOSUBMIT_AGENT_TOKEN,
        allow_http_hosts=AUTOSUBMIT_AGENT_ALLOW_HTTP,
    )
    with SessionLocal() as session:
        dispatch_due_submissions(session, agent, warmup_lead=AUTOSUBMIT_WARMUP_LEAD)
