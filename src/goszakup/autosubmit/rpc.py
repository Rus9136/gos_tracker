"""Контракт RPC между Linux-планировщиком и Windows submit-agent.

Пред-стейдж невозможен → агент исполняет ВСЮ гонку: прогрев (логин, страница
объявления, разблокировка PIN), ожидание `open_at`, визард с реальным Tumar,
подача. Linux лишь ставит задачу и принимает отчёт.

БЕЗОПАСНОСТЬ: `RunRequest` несёт РАСШИФРОВАННЫЕ секреты клиента (мастер-ключ
KeyVault — на Linux). Транспорт ОБЯЗАН быть приватным (Tailscale/WireGuard +
mTLS), агент не должен слушать публично, секреты не логируются (гайд §8.3).
Здесь — только форма данных и (де)сериализация; HTTP-сервер агента — Phase 2.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LotBid:
    lot_id: int
    price: str  # Decimal как строка — без потери точности по сети


@dataclass(frozen=True)
class RunRequest:
    submission_id: int
    anno_id: int
    open_at_iso: str
    lot_bids: list[LotBid]
    # секреты клиента (по VPN/mTLS, не логировать)
    p12_b64: str
    portal_password: str
    key_pin: str | None = None
    anno_number: str | None = None
    close_at_iso: str | None = None
    # server−local, сек (если измерено точнее NTP); упреждение выстрела, сек
    clock_offset: float = 0.0
    fire_lead: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunRequest:
        bids = [LotBid(**b) for b in d.get("lot_bids", [])]
        return cls(**{**d, "lot_bids": bids})


@dataclass(frozen=True)
class RunResult:
    submission_id: int
    status: str  # одно из SUBMISSION_STATUSES
    app_id: int | None = None
    fire_latency_ms: int | None = None
    fired_at_iso: str | None = None
    submitted_at_iso: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunResult:
        return cls(**d)


@dataclass(frozen=True)
class StatusReport:
    """Промежуточный пинг прогрева/прогресса агента (для UI «идёт подача #N»)."""

    submission_id: int
    status: str
    detail: str | None = None
    at_iso: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
