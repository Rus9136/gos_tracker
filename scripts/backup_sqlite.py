"""Безопасный бэкап SQLite через `sqlite3.backup()` + ротация.

Запускается systemd-таймером (`goszakup-backup.timer`) после `goszakup-daily`.
Использует `Connection.backup()` — корректно работает на живом WAL-файле и
не требует останова writer'а (в отличие от cp/rsync на .sqlite-файле).

Хранит последние KEEP_LAST бэкапов, остальные удаляет. Без сжатия —
бэкап и так маленький на этой стадии (десятки МБ), а Postgres-фаза этот
скрипт всё равно заменит на pg_basebackup.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Зависим от внутренних путей приложения, чтобы один и тот же путь к БД
# использовался скриптом и сервером.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from goszakup.config import DATA_DIR, DB_PATH  # noqa: E402

BACKUP_DIR = DATA_DIR / "backups"
KEEP_LAST = 14

log = logging.getLogger("backup_sqlite")


def make_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    dst_path = BACKUP_DIR / f"goszakup-{stamp}.sqlite"

    # sqlite3.Connection.backup() — atomic copy через online backup API.
    # При наличии writer'а будет переждать его транзакцию.
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(dst_path) as dst:
        src.backup(dst)
    log.info("backup created: %s (%d bytes)", dst_path, dst_path.stat().st_size)
    return dst_path


def rotate() -> None:
    backups = sorted(BACKUP_DIR.glob("goszakup-*.sqlite"))
    excess = len(backups) - KEEP_LAST
    if excess <= 0:
        return
    for path in backups[:excess]:
        try:
            path.unlink()
            log.info("rotated out: %s", path.name)
        except OSError as e:
            log.warning("не удалось удалить %s: %s", path, e)


def main() -> int:
    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if not Path(DB_PATH).exists():
        log.error("БД не найдена: %s", DB_PATH)
        return 1
    try:
        make_backup()
        rotate()
    except Exception:
        log.exception("backup failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
