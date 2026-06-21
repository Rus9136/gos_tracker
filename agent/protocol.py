"""Протокол Linux↔agent. ЗЕРКАЛО `goszakup.autosubmit.rpc` — держать в синхроне.

Дублируется (а не импортируется), чтобы агент был самодостаточным на Windows и
не тащил весь пакет goszakup (sqlalchemy/fastapi/...). Поля и имена обязаны
совпадать с `src/goszakup/autosubmit/rpc.py`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LotBid:
    lot_id: int
    price: str


@dataclass(frozen=True)
class RunRequest:
    submission_id: int
    anno_id: int
    open_at_iso: str
    lot_bids: list[LotBid]
    p12_b64: str
    portal_password: str
    key_pin: str | None = None
    anno_number: str | None = None
    close_at_iso: str | None = None
    clock_offset: float = 0.0
    fire_lead: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunRequest:
        bids = [LotBid(**b) for b in d.get("lot_bids", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{**{k: v for k, v in d.items() if k in known}, "lot_bids": bids})


@dataclass
class RunResult:
    submission_id: int
    status: str  # PLANNED|ARMED|FIRING|SUBMITTED|CONFIRMED|FAILED|SKIPPED
    app_id: int | None = None
    fire_latency_ms: int | None = None
    fired_at_iso: str | None = None
    submitted_at_iso: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StatusReport:
    submission_id: int
    status: str
    detail: str | None = None
    at_iso: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
