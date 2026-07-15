"""HTTP-клиент к Windows submit-agent (RPC по приватной сети).

Тонкая обёртка: диспетчер ставит агенту задачу (`dispatch`) и сразу возвращается
— агент долго прогревается и ждёт `open_at`, потом сам присылает `RunResult` на
ingest-эндпоинт Linux (Phase 2). Транспорт обязан быть приватным (Tailscale/
WireGuard + mTLS) — `RunRequest` несёт расшифрованные секреты клиента.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .rpc import RunRequest


class AgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentClient:
    base_url: str
    token: str | None = None
    timeout: float = 15.0
    # Хосты, которым разрешён plain-HTTP (приватный tailnet). По умолчанию пусто —
    # секреты уходят только по https, если хост не в allowlist явно.
    allow_http_hosts: tuple[str, ...] = field(default_factory=tuple)

    def _headers(self) -> dict[str, str]:
        return {"X-Agent-Token": self.token} if self.token else {}

    def _validate_transport(self) -> None:
        """RunRequest несёт расшифрованные p12/пароль/PIN — не слать их без
        авторизации и по незашифрованному каналу (кроме явного allowlist)."""
        if not self.token:
            raise AgentError(
                "GZ_AUTOSUBMIT_AGENT_TOKEN обязателен: отказ слать секреты клиента "
                "без авторизации канала"
            )
        scheme = urlsplit(self.base_url).scheme
        if scheme == "https":
            return
        host = urlsplit(self.base_url).hostname
        if scheme == "http" and host in self.allow_http_hosts:
            return
        raise AgentError(
            f"небезопасный транспорт до агента {self.base_url!r}: нужен https либо "
            f"host в allowlist ({self.allow_http_hosts or '—'})"
        )

    def dispatch(self, req: RunRequest) -> dict:
        """Поставить агенту задачу на прогрев+гонку. Возвращает ack агента."""
        self._validate_transport()
        try:
            resp = httpx.post(
                f"{self.base_url.rstrip('/')}/run",
                json=req.to_dict(),
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise AgentError(f"dispatch failed: {type(e).__name__}: {e}") from e

    def health(self) -> bool:
        try:
            resp = httpx.get(f"{self.base_url.rstrip('/')}/health", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
