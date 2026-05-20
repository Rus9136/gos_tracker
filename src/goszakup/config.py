"""Глобальные константы и пути."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"
DB_PATH = DATA_DIR / "goszakup.sqlite"
DB_URL = f"sqlite:///{DB_PATH}"

# `config` импортируется всеми точками входа (CLI, web, jobs), поэтому
# подгрузка .env здесь гарантирует доступность ключей до первого
# обращения к os.environ. override=False — реальный shell-env приоритетнее.
load_dotenv(ROOT / ".env", override=False)

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


def announcement_url(anno_id: int | str) -> str:
    return f"{ANNOUNCE_URL}/{anno_id}"


DATA_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)
