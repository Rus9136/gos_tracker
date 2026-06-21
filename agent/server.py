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

        threading.Thread(target=run, args=(req,), name=f"submit-{req.submission_id}", daemon=True).start()
        log.info("accepted run #%s (anno=%s)", req.submission_id, req.anno_id)
        self._send(202, {"ack": True, "submission_id": req.submission_id})


def serve() -> None:
    httpd = ThreadingHTTPServer((config.AGENT_HOST, config.AGENT_PORT), _Handler)
    log.info("submit-agent слушает http://%s:%s (token=%s)",
             config.AGENT_HOST, config.AGENT_PORT, "on" if config.AGENT_TOKEN else "OFF")
    httpd.serve_forever()
