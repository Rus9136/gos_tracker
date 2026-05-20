"""Общий conftest для pytest.

Изолируем тесты от боевого `data/goszakup.sqlite`: подменяем DB_URL на
tmp-файл ДО любого импорта `goszakup.*`. Это безопасно — `setdefault`
сохраняет явные переменные окружения (например, для интеграционных
прогонов на Postgres).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="goszakup-test-"))
os.environ.setdefault("GZ_DATABASE_URL", f"sqlite:///{_TEST_DB_DIR}/test.sqlite")
os.environ.setdefault("GZ_NO_AUTH", "1")
os.environ.setdefault("GZ_TEST_MODE", "1")


def pytest_configure(config):  # noqa: ARG001
    # Если кто-то нечаянно прогонит интеграционный тест локально с боевой
    # БД — лучше упасть на старте. Явные тесты сами перетирают переменную.
    os.environ.setdefault("GZ_TEST_MODE", "1")
