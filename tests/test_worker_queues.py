"""P0-2: воркер обязан слушать все очереди, объявленные actor'ами.

Регресс на «разомкнутый контур»: actor автоподачи объявляет очередь
`goszakup_autosubmit`, но если её нет в `--queues` systemd-юнита (или actor
не импортируется воркерным модулем), задачи молча копятся в Redis и дропаются
по AgeLimit. Тест сравнивает множество очередей в systemd-шаблоне с множеством
очередей, реально зарегистрированных в брокере.
"""

from __future__ import annotations

from pathlib import Path

# Импорт регистрирует ВСЕ actor'ы (в т.ч. matching/notify/autosubmit) в брокере —
# ровно то, что грузит воркер через `dramatiq goszakup.queue.actors`.
import goszakup.queue.actors  # noqa: F401
from goszakup.queue.broker import broker

_UNIT = Path(__file__).resolve().parents[1] / "scripts" / "systemd" / "goszakup-worker.service"


def _declared_queues() -> set[str]:
    try:
        declared = set(broker.get_declared_queues())
    except AttributeError:  # старые версии dramatiq
        declared = set(broker.queues)
    return {q for q in declared if q.startswith("goszakup_")}


def _systemd_queues() -> set[str]:
    text = _UNIT.read_text(encoding="utf-8")
    assert "--queues" in text, "в юните воркера нет флага --queues"
    after = text.split("--queues", 1)[1]
    return {tok for tok in after.split() if tok.startswith("goszakup_")}


def test_systemd_serves_all_declared_queues():
    declared = _declared_queues()
    systemd = _systemd_queues()
    missing = declared - systemd
    assert not missing, (
        f"очереди объявлены actor'ами, но не в --queues воркера: {sorted(missing)}"
    )


def test_autosubmit_queue_present():
    # Явная точка регресса P0-2.
    assert "goszakup_autosubmit" in _declared_queues()
    assert "goszakup_autosubmit" in _systemd_queues()
