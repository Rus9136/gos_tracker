"""Общий conftest для pytest.

Изолируем тесты от боевого `data/goszakup.sqlite`: по умолчанию подменяем
DB_URL на временный файл per-test, если тест явно его не запросил.
"""

from __future__ import annotations

import os


def pytest_configure(config):  # noqa: ARG001
    # Если кто-то нечаянно прогонит интеграционный тест локально с боевой
    # БД — лучше упасть на старте. Явные тесты сами перетирают переменную.
    os.environ.setdefault("GZ_TEST_MODE", "1")
