"""Глобальные константы и пути."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

# `config` импортируется всеми точками входа (CLI, web, jobs), поэтому
# подгрузка .env здесь гарантирует доступность ключей до первого
# обращения к os.environ. override=False — реальный shell-env приоритетнее.
load_dotenv(ROOT / ".env", override=False)

# В Docker-образе пакет ставится в site-packages, `ROOT` указывает на
# `/usr/local/lib/python3.12` — туда писать нельзя. Поэтому DATA_DIR можно
# переопределить через env. На дев-машине дефолт = `<repo>/data`.
DATA_DIR = Path(os.environ.get("GZ_DATA_DIR") or (ROOT / "data"))
DOCS_DIR = DATA_DIR / "docs"
DB_PATH = DATA_DIR / "goszakup.sqlite"

# GZ_DATABASE_URL переопределяет дефолт. Используется тестами (tmp-файл),
# Alembic autogenerate (пустая БД) и будущим переездом на Postgres.
DB_URL = os.environ.get("GZ_DATABASE_URL", f"sqlite:///{DB_PATH}")

# SOCKS/HTTP-прокси (туннель в KZ) для запросов к goszakup. Исходно был
# только для v3bl.goszakup.gov.kz (403-геоблок по ASN на nginx), но с
# 2026-07-16 и основной goszakup.gov.kz начал массово дропать TCP-коннекты
# с нашего FR-IP (перемежающиеся ConnectTimeout), поэтому через туннель
# идёт ВЕСЬ трафик скрейперных сессий (см. build_goszakup_session).
# См. systemd-юнит goszakup-tunnel.service.
GZ_PROXY_URL = os.environ.get("GZ_PROXY_URL") or None

# Минимальная сумма лота, ниже которой не отслеживаем (зафиксировано продуктовым решением).
MIN_AMOUNT = 500_000

# Тариф LLM для ОЦЕНКИ стоимости на странице /expenses (USD за 1M токенов).
# Токены учитываются точно (LlmCall), деньги — прикидка по этим ставкам;
# на free-tier Cerebras фактический счёт = $0. Дефолт — платный прайс
# gpt-oss-120b. Переопределяется через env, если сменится тариф/провайдер.
LLM_PRICE_INPUT_PER_MTOK = float(os.environ.get("GZ_LLM_PRICE_INPUT", "0.25"))
LLM_PRICE_OUTPUT_PER_MTOK = float(os.environ.get("GZ_LLM_PRICE_OUTPUT", "0.69"))

# Токен Telegram-бота для уведомлений о новых подходящих лотах. Один общий бот
# на весь сервис; каждый пользователь сохраняет свой chat_id в /settings. Если
# не задан — уведомления тихо отключены (notify/telegram.py логирует и выходит).
GZ_TELEGRAM_BOT_TOKEN = os.environ.get("GZ_TELEGRAM_BOT_TOKEN") or None

# Секрет Telegram-вебхука (кнопка «Подробнее» в уведомлении). Передаётся в
# setWebhook как secret_token — Telegram шлёт его обратно в заголовке
# X-Telegram-Bot-Api-Secret-Token, по нему web отличает Telegram от произвольных
# POST'ов. Без него POST /telegram/webhook выключен (503). Установка вебхука —
# `cli telegram-set-webhook` (одноразово после добавления секрета в .env).
TELEGRAM_WEBHOOK_SECRET = os.environ.get("GZ_TELEGRAM_WEBHOOK_SECRET") or None

# Публичный адрес UI — для ссылки на карточку лота в Telegram-уведомлении.
# На проде это https://gost.salemsoft.kz; на dev можно переопределить.
PUBLIC_BASE_URL = (
    os.environ.get("GZ_PUBLIC_BASE_URL") or "https://gost.salemsoft.kz"
).rstrip("/")

# Секрет для подписи cookie-сессии (Starlette SessionMiddleware, форма входа
# /login). На проде ОБЯЗАТЕЛЕН в .env — иначе при каждом рестарте генерится
# новый и все сессии инвалидируются. Дефолт — только для dev/тестов.
_INSECURE_SECRET_DEFAULT = "dev-insecure-change-me"
SECRET_KEY = os.environ.get("GZ_SECRET_KEY") or _INSECURE_SECRET_DEFAULT


def secret_key_is_safe() -> bool:
    """True, если GZ_SECRET_KEY задан и не равен публичному dev-дефолту."""
    return SECRET_KEY != _INSECURE_SECRET_DEFAULT


def _is_dev_mode() -> bool:
    # GZ_NO_AUTH=1 — явный dev-режим без логина; GZ_TEST_MODE=1 ставит conftest.
    return os.environ.get("GZ_NO_AUTH") == "1" or os.environ.get("GZ_TEST_MODE") == "1"


def require_safe_secret_key(component: str) -> None:
    """Fail-fast на старте боевого процесса с небезопасным SECRET_KEY.

    Тихая деградация на публичный дефолт (репозиторий открыт) = обход auth:
    любой подделает подписанную cookie с чужим uid. Поэтому web и worker
    отказываются стартовать без реального ключа, кроме явного dev-режима.
    """
    if secret_key_is_safe() or _is_dev_mode():
        return
    raise RuntimeError(
        f"{component}: GZ_SECRET_KEY не задан или равен небезопасному дефолту "
        f"'{_INSECURE_SECRET_DEFAULT}'. Сгенерируйте случайный ключ "
        "(openssl rand -hex 32) и задайте GZ_SECRET_KEY в .env. "
        "Для локального dev-режима без логина — GZ_NO_AUTH=1."
    )

# --- Автоподача заявок (TENDER_AUTOSUBMIT_PLAN.md) -----------------------------
# Мастер-ключ KeyVault (.p12/пароли/PIN клиентов) читается напрямую в
# vault/crypto.py из GZ_VAULT_MASTER_KEY (base64 от 32 байт). В проде должен
# приходить из KMS/HSM. Без него обращение к Vault падает с понятной ошибкой.
# Адрес Windows submit-agent по приватной сети (Tailscale/WireGuard). Без него
# диспетчер автоподачи отключён (нечему слать RunRequest).
AUTOSUBMIT_AGENT_URL = os.environ.get("GZ_AUTOSUBMIT_AGENT_URL") or None
# Токен авторизации Linux→agent (`POST /run`, заголовок X-Agent-Token). Должен
# совпасть с GZ_AGENT_TOKEN на Windows-узле. Обязателен: без него диспетчер
# отказывается слать секреты клиента (см. autosubmit/agent_client.py).
AUTOSUBMIT_AGENT_TOKEN = os.environ.get("GZ_AUTOSUBMIT_AGENT_TOKEN") or None
# Хосты, которым разрешён plain-HTTP до агента (приватный tailnet, где TLS даёт
# WireGuard/Tailscale). Пусто = только https. Пример: "100.64.0.5,agent.tailnet".
AUTOSUBMIT_AGENT_ALLOW_HTTP = tuple(
    h.strip()
    for h in (os.environ.get("GZ_AUTOSUBMIT_AGENT_ALLOW_HTTP") or "").split(",")
    if h.strip()
)
# За сколько секунд до open_at слать задачу агенту на прогрев (логин, страница
# объявления, разблокировка PIN), чтобы к открытию он уже ждал кнопку «Подать».
AUTOSUBMIT_WARMUP_LEAD = int(os.environ.get("GZ_AUTOSUBMIT_WARMUP_LEAD", "300"))
# Общий токен для ingest-эндпоинта `POST /autosubmit/result` (агент шлёт сюда
# RunResult). Машинная аутентификация, не cookie-сессия. Без токена ingest
# отключён (агенту некуда отчитываться через web).
AUTOSUBMIT_INGEST_TOKEN = os.environ.get("GZ_AUTOSUBMIT_INGEST_TOKEN") or None

# Задержка между HTTP-запросами (Crawl-delay из robots.txt goszakup).
CRAWL_DELAY = 5.0

# Официальный API goszakup (OWS, ows.goszakup.gov.kz). Токен выдаёт ЦЭФ на
# 1 год; без токена источником данных остаётся HTML-скрейпинг
# (см. sources.make_source).
OWS_TOKEN = os.environ.get("GZ_OWS_TOKEN") or None
OWS_BASE_URL = (
    os.environ.get("GZ_OWS_BASE_URL") or "https://ows.goszakup.gov.kz"
).rstrip("/")
# Пауза между запросами к OWS. Лимит API нигде не заявлен (бурст 6+ rps
# проходил без 429), 1 rps — консервативный запас. Независим от CRAWL_DELAY:
# у HTML-скрейпера контракт robots.txt, у API его нет.
API_DELAY = float(os.environ.get("GZ_API_DELAY", "1.0"))
# ISO-дата истечения токена (YYYY-MM-DD) — health-check предупреждает за 14
# дней. Истёкший токен ows маскирует под 404 «Invalid Route», сам он не виден.
OWS_TOKEN_EXPIRES = os.environ.get("GZ_OWS_TOKEN_EXPIRES") or ""
# OWS доступен с зарубежного IP напрямую (в отличие от goszakup.gov.kz) —
# ходим без туннеля, чтобы его не грузить. Флаг — закладка на случай геоблока.
OWS_USE_PROXY = os.environ.get("GZ_OWS_USE_PROXY", "").lower() in ("1", "true", "yes")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HTTP_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"}

# 2026-09: портал переехал на zakup.gov.kz, старый хост отдаёт 403 на все
# пути. Прежний интерфейс (и все скрейперные пути ниже) живёт на old.*,
# он же отвечает с зарубежного IP напрямую, без KZ-туннеля.
BASE_URL = "https://old.goszakup.gov.kz"
SEARCH_URL = f"{BASE_URL}/ru/search/lots"
ANNOUNCE_URL = f"{BASE_URL}/ru/announce/index"
# Карточка ценового предложения лота: единственная страница с цифровым «Код ТРУ».
# Путь /{trd_buy_id}/{lot_id}; trd_buy_id == announcement_id (совпадают на goszakup).
SUBPRICEOFFER_URL = f"{BASE_URL}/ru/subpriceoffer/index"


def announcement_url(anno_id: int | str) -> str:
    return f"{ANNOUNCE_URL}/{anno_id}"


DATA_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)
