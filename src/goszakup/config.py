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

# SOCKS/HTTP-прокси для запросов к v3bl.goszakup.gov.kz. С нашего FR-IP
# v3bl возвращает 403 на уровне nginx (геоблок по ASN), поэтому файлы
# скачиваем через туннель в KZ. Применяется ТОЛЬКО к v3bl-хосту —
# листинги и страницы объявлений идут напрямую (быстрее, без лишней
# нагрузки на туннель). См. systemd-юнит goszakup-tunnel.service.
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

# Публичный адрес UI — для ссылки на карточку лота в Telegram-уведомлении.
# На проде это https://gost.salemsoft.kz; на dev можно переопределить.
PUBLIC_BASE_URL = (
    os.environ.get("GZ_PUBLIC_BASE_URL") or "https://gost.salemsoft.kz"
).rstrip("/")

# Секрет для подписи cookie-сессии (Starlette SessionMiddleware, форма входа
# /login). На проде ОБЯЗАТЕЛЕН в .env — иначе при каждом рестарте генерится
# новый и все сессии инвалидируются. Дефолт — только для dev/тестов.
SECRET_KEY = os.environ.get("GZ_SECRET_KEY") or "dev-insecure-change-me"

# --- Автоподача заявок (TENDER_AUTOSUBMIT_PLAN.md) -----------------------------
# Мастер-ключ KeyVault (.p12/пароли/PIN клиентов) читается напрямую в
# vault/crypto.py из GZ_VAULT_MASTER_KEY (base64 от 32 байт). В проде должен
# приходить из KMS/HSM. Без него обращение к Vault падает с понятной ошибкой.
# Адрес Windows submit-agent по приватной сети (Tailscale/WireGuard). Без него
# диспетчер автоподачи отключён (нечему слать RunRequest).
AUTOSUBMIT_AGENT_URL = os.environ.get("GZ_AUTOSUBMIT_AGENT_URL") or None
# Токен авторизации Linux→agent (`POST /run`, заголовок X-Agent-Token). Должен
# совпасть с GZ_AGENT_TOKEN на Windows-узле. Защита в глубину поверх tailnet.
AUTOSUBMIT_AGENT_TOKEN = os.environ.get("GZ_AUTOSUBMIT_AGENT_TOKEN") or None
# За сколько секунд до open_at слать задачу агенту на прогрев (логин, страница
# объявления, разблокировка PIN), чтобы к открытию он уже ждал кнопку «Подать».
AUTOSUBMIT_WARMUP_LEAD = int(os.environ.get("GZ_AUTOSUBMIT_WARMUP_LEAD", "300"))
# Общий токен для ingest-эндпоинта `POST /autosubmit/result` (агент шлёт сюда
# RunResult). Машинная аутентификация, не cookie-сессия. Без токена ingest
# отключён (агенту некуда отчитываться через web).
AUTOSUBMIT_INGEST_TOKEN = os.environ.get("GZ_AUTOSUBMIT_INGEST_TOKEN") or None

# Задержка между HTTP-запросами (Crawl-delay из robots.txt goszakup).
CRAWL_DELAY = 5.0

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HTTP_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"}

BASE_URL = "https://goszakup.gov.kz"
SEARCH_URL = f"{BASE_URL}/ru/search/lots"
ANNOUNCE_URL = f"{BASE_URL}/ru/announce/index"
# Карточка ценового предложения лота: единственная страница с цифровым «Код ТРУ».
# Путь /{trd_buy_id}/{lot_id}; trd_buy_id == announcement_id (совпадают на goszakup).
SUBPRICEOFFER_URL = f"{BASE_URL}/ru/subpriceoffer/index"


def announcement_url(anno_id: int | str) -> str:
    return f"{ANNOUNCE_URL}/{anno_id}"


DATA_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)
