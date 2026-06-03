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
