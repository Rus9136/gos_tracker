"""P0-3a: dispatch_due_submissions — застолбить подачу ДО отправки агенту.

Гонка двух тиков диспетчера / редоставка actor'а не должна привести к двойной
отправке одной подачи. Механизм: claim (перевод PLANNED→DISPATCHING + commit)
до вызова agent.dispatch(). Проверяем поведение детерминированно на SQLite;
ветка FOR UPDATE SKIP LOCKED — только под Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from goszakup.autosubmit import scheduler as scheduler_mod
from goszakup.autosubmit.agent_client import AgentError
from goszakup.autosubmit.rpc import RunResult
from goszakup.autosubmit.scheduler import apply_result, dispatch_due_submissions
from goszakup.db.models import ClientCredential, Submission


@pytest.fixture(autouse=True)
def _stub_run_request(monkeypatch):
    # Изолируем диспетчер от vault-расшифровки — тестируем только статус-машину.
    monkeypatch.setattr(scheduler_mod, "build_run_request", lambda sub: {"sub": sub.id})


@pytest.fixture(autouse=True)
def _clean_autosubmit(db_session):
    # conftest.db_session не чистит submissions/client_credentials — делаем сами.
    db_session.query(Submission).delete()
    db_session.query(ClientCredential).delete()
    db_session.commit()


class _CountingAgent:
    def __init__(self, *, fail=False, on_dispatch=None):
        self.calls: list[int] = []
        self.fail = fail
        self.on_dispatch = on_dispatch

    def dispatch(self, req):
        self.calls.append(req["sub"])
        if self.on_dispatch:
            self.on_dispatch()
        if self.fail:
            raise AgentError("agent down")
        return {"ack": True}


def _mk_submission(session, *, sub_id, open_at, status="PLANNED", close_at=None):
    cred = ClientCredential(
        label="t",
        p12_enc="x",
        p12_nonce="n",
        portal_password_enc="x",
        portal_password_nonce="n",
    )
    session.add(cred)
    session.flush()
    sub = Submission(
        id=sub_id,
        credential_id=cred.id,
        anno_id=999,
        bid_enc="e",
        bid_nonce="n",
        open_at=open_at,
        close_at=close_at,
        status=status,
    )
    session.add(sub)
    session.commit()
    return sub


def test_dispatch_arms_due_submission(db_session):
    due = datetime.now(UTC) + timedelta(seconds=60)
    _mk_submission(db_session, sub_id=1, open_at=due)
    agent = _CountingAgent()

    armed = dispatch_due_submissions(db_session, agent, warmup_lead=300)

    assert armed == [1]
    assert agent.calls == [1]
    sub = db_session.get(Submission, 1)
    assert sub.status == "ARMED"
    assert sub.attempts == 1


def test_not_due_submission_untouched(db_session):
    far = datetime.now(UTC) + timedelta(hours=2)
    _mk_submission(db_session, sub_id=1, open_at=far)
    agent = _CountingAgent()

    armed = dispatch_due_submissions(db_session, agent, warmup_lead=300)

    assert armed == []
    assert agent.calls == []
    assert db_session.get(Submission, 1).status == "PLANNED"


def test_second_tick_does_not_redispatch(db_session):
    due = datetime.now(UTC) + timedelta(seconds=60)
    _mk_submission(db_session, sub_id=1, open_at=due)
    agent = _CountingAgent()

    dispatch_due_submissions(db_session, agent, warmup_lead=300)
    dispatch_due_submissions(db_session, agent, warmup_lead=300)  # редоставка/второй тик

    assert agent.calls == [1]  # ровно один раз


def test_claim_committed_before_dispatch(db_session):
    """Реентрантный тик ВНУТРИ agent.dispatch не должен подхватить ту же подачу.

    Это доказывает, что DISPATCHING зафиксирован в БД ДО вызова agent.dispatch:
    вложенный dispatch_due_submissions уже не видит PLANNED.
    """
    due = datetime.now(UTC) + timedelta(seconds=60)
    _mk_submission(db_session, sub_id=1, open_at=due)

    inner = _CountingAgent()

    def reentrant_tick():
        dispatch_due_submissions(db_session, inner, warmup_lead=300)

    outer = _CountingAgent(on_dispatch=reentrant_tick)
    dispatch_due_submissions(db_session, outer, warmup_lead=300)

    assert outer.calls == [1]
    assert inner.calls == []  # вложенный тик не нашёл PLANNED — claim сработал


def test_agent_error_returns_to_planned_then_failed(db_session):
    due = datetime.now(UTC) + timedelta(seconds=60)
    _mk_submission(db_session, sub_id=1, open_at=due)
    agent = _CountingAgent(fail=True)

    # Первые тики возвращают в PLANNED (повторим), после лимита — FAILED.
    for _ in range(scheduler_mod.MAX_DISPATCH_ATTEMPTS - 1):
        dispatch_due_submissions(db_session, agent, warmup_lead=300)
        assert db_session.get(Submission, 1).status == "PLANNED"

    dispatch_due_submissions(db_session, agent, warmup_lead=300)
    sub = db_session.get(Submission, 1)
    assert sub.status == "FAILED"
    assert sub.attempts == scheduler_mod.MAX_DISPATCH_ATTEMPTS


def test_expired_close_at_is_skipped_not_dispatched(db_session):
    due = datetime.now(UTC) + timedelta(seconds=60)
    past_close = datetime.now(UTC) - timedelta(minutes=1)
    _mk_submission(db_session, sub_id=1, open_at=due, close_at=past_close)
    agent = _CountingAgent()

    armed = dispatch_due_submissions(db_session, agent, warmup_lead=300)

    assert armed == []
    assert agent.calls == []  # агенту не отправляли
    assert db_session.get(Submission, 1).status == "SKIPPED"


# --- apply_result: forward-only (P0-apply) ---------------------------------


def _mk_sub_status(db_session, status):
    due = datetime.now(UTC) + timedelta(seconds=60)
    _mk_submission(db_session, sub_id=1, open_at=due, status=status)


def test_late_result_does_not_overwrite_confirmed(db_session):
    _mk_sub_status(db_session, "CONFIRMED")
    apply_result(db_session, RunResult(submission_id=1, status="SUBMITTED"))
    assert db_session.get(Submission, 1).status == "CONFIRMED"


def test_duplicate_confirmed_is_idempotent(db_session):
    _mk_sub_status(db_session, "CONFIRMED")
    apply_result(db_session, RunResult(submission_id=1, status="CONFIRMED", app_id=77))
    sub = db_session.get(Submission, 1)
    assert sub.status == "CONFIRMED"
    # терминальный статус не трогаем — app_id из позднего дубля не записываем
    assert sub.app_id is None


def test_forward_transition_applies(db_session):
    _mk_sub_status(db_session, "ARMED")
    apply_result(db_session, RunResult(submission_id=1, status="SUBMITTED", app_id=5))
    sub = db_session.get(Submission, 1)
    assert sub.status == "SUBMITTED"
    assert sub.app_id == 5


def test_backward_nonterminal_is_rejected(db_session):
    _mk_sub_status(db_session, "SUBMITTED")
    apply_result(db_session, RunResult(submission_id=1, status="ARMED"))
    assert db_session.get(Submission, 1).status == "SUBMITTED"


def test_unknown_applies_over_nonterminal(db_session):
    _mk_sub_status(db_session, "FIRING")
    apply_result(db_session, RunResult(submission_id=1, status="UNKNOWN", error="read timeout"))
    sub = db_session.get(Submission, 1)
    assert sub.status == "UNKNOWN"
    assert sub.error == "read timeout"
