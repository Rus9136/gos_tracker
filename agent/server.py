"""Минимальный HTTP-сервер агента (stdlib, без веб-фреймворка).

Эндпоинты:
- GET  /health → 200 {"ok": true}
- POST /run    → принять RunRequest, запустить подачу в фоне, вернуть 202.

`/run` сразу возвращает ack и обрабатывает задачу в отдельном потоке: подача
длится минуты (прогрев + ожидание open_at + визард), блокировать ответ нельзя.
Авторизация — заголовок X-Agent-Token (если GZ_AGENT_TOKEN задан). Слушать
только на приватном интерфейсе (GZ_AGENT_HOST).
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .protocol import RunRequest
from .runner import run

log = logging.getLogger(__name__)

# Дедуп подач по submission_id (P0-3b). Диспетчер на Linux может отправить /run
# повторно (редоставка actor'а, ретрай после ложного таймаута) — второй прогон
# той же подачи запустил бы ВТОРУЮ гонку и подал бы заявку дважды. Реестр держит
# state по submission_id на время жизни процесса; повторный /run с тем же id не
# стартует новый поток. Терминальный state остаётся — повторную подачу уже
# завершённой заявки тоже не запускаем.
_active_lock = threading.Lock()
_active: dict[int, str] = {}


def _run_and_track(req: RunRequest) -> None:
    try:
        run(req)
    finally:
        with _active_lock:
            _active[req.submission_id] = "done"


def _accept_run(req: RunRequest) -> bool:
    """True — запущен новый прогон; False — дубль (submission_id уже принят)."""
    with _active_lock:
        if req.submission_id in _active:
            return False
        _active[req.submission_id] = "running"
    threading.Thread(
        target=_run_and_track, args=(req,), name=f"submit-{req.submission_id}", daemon=True
    ).start()
    return True


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # перенаправить в logging вместо stderr
        log.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send(404, {"error": "not found"})
            return
        if config.AGENT_TOKEN:
            sent = self.headers.get("X-Agent-Token", "")
            if not hmac.compare_digest(sent, config.AGENT_TOKEN):
                self._send(401, {"error": "unauthorized"})
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            req = RunRequest.from_dict(payload)
        except (ValueError, TypeError, KeyError) as e:
            self._send(400, {"error": f"bad payload: {e}"})
            return

        if not _accept_run(req):
            log.info("run #%s уже принят — дубль /run игнорирован", req.submission_id)
            with _active_lock:
                state = _active.get(req.submission_id, "unknown")
            self._send(
                200,
                {"ack": True, "submission_id": req.submission_id, "duplicate": True, "state": state},
            )
            return
        log.info("accepted run #%s (anno=%s)", req.submission_id, req.anno_id)
        self._send(202, {"ack": True, "submission_id": req.submission_id})


def serve() -> None:
    httpd = ThreadingHTTPServer((config.AGENT_HOST, config.AGENT_PORT), _Handler)
    log.info("submit-agent слушает http://%s:%s (token=%s)",
             config.AGENT_HOST, config.AGENT_PORT, "on" if config.AGENT_TOKEN else "OFF")
    httpd.serve_forever()
