"""HTTP-клиент к Windows submit-agent (RPC по приватной сети).

Тонкая обёртка: диспетчер ставит агенту задачу (`dispatch`) и сразу возвращается
— агент долго прогревается и ждёт `open_at`, потом сам присылает `RunResult` на
ingest-эндпоинт Linux (Phase 2). Транспорт обязан быть приватным (Tailscale/
WireGuard + mTLS) — `RunRequest` несёт расшифрованные секреты клиента.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .rpc import RunRequest


class AgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentClient:
    base_url: str
    token: str | None = None
    timeout: float = 15.0

    def _headers(self) -> dict[str, str]:
        return {"X-Agent-Token": self.token} if self.token else {}

    def dispatch(self, req: RunRequest) -> dict:
        """Поставить агенту задачу на прогрев+гонку. Возвращает ack агента."""
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
