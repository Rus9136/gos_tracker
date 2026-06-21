"""Конфигурация Windows submit-agent (из окружения; .env.example рядом).

Все настройки — через env, чтобы один и тот же код работал на dev-компе и на
боевом узле. Секреты (токены) — только в env, не в коде.
"""

from __future__ import annotations

import os

# --- сеть/сервер агента -------------------------------------------------------
# Слушать только на tailnet-интерфейсе (приватная сеть Tailscale/WireGuard), не
# на 0.0.0.0 — у агента доступ к ключу клиента, наружу торчать нельзя.
AGENT_HOST = os.environ.get("GZ_AGENT_HOST", "127.0.0.1")
AGENT_PORT = int(os.environ.get("GZ_AGENT_PORT", "8799"))
# Токен, которым Linux-планировщик авторизуется в `POST /run` (защита в глубину
# поверх приватной сети). Если не задан — проверка выключена (только для dev).
AGENT_TOKEN = os.environ.get("GZ_AGENT_TOKEN") or None

# --- отчёт обратно на Linux ---------------------------------------------------
# Куда слать RunResult по завершении (ingest-эндпоинт Linux) и токен для него.
LINUX_INGEST_URL = os.environ.get("GZ_LINUX_INGEST_URL") or None
INGEST_TOKEN = os.environ.get("GZ_AUTOSUBMIT_INGEST_TOKEN") or None

# --- goszakup / локальные крипто-провайдеры ----------------------------------
GOSZAKUP_BASE = os.environ.get("GZ_GOSZAKUP_BASE", "https://v3bl.goszakup.gov.kz")
NCALAYER_WS = os.environ.get("GZ_NCALAYER_WS", "wss://127.0.0.1:13579/")
TUMAR_WS = os.environ.get("GZ_TUMAR_WS", "wss://127.0.0.1:6127/tumarcsp/")
# Заголовок нативного окна ввода цены Tumar — ЗАПОЛНИТЬ ИЗ RECON (шаг 3 README):
# открыть руками до шага цены и посмотреть точный заголовок окна.
TUMAR_PRICE_WINDOW_TITLE = os.environ.get("GZ_TUMAR_WINDOW_TITLE", "")

# --- браузер/тайминги ---------------------------------------------------------
# Боевой режим — НЕ headless: NCALayer/Tumar открывают нативные окна, нужен
# реальный десктоп. headless=1 только для отладки кусков без крипто.
HEADLESS = os.environ.get("GZ_AGENT_HEADLESS", "0") == "1"
# Запас перед open_at на прогрев (логин+навигация), сек. Должен быть меньше
# warmup-lead планировщика (дефолт 300с).
WARMUP_MARGIN = int(os.environ.get("GZ_AGENT_WARMUP_MARGIN", "120"))
