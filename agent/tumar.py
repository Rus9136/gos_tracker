"""Управление нативным окном ввода цены Tumar CSP через pywinauto.

ПОЧЕМУ ВООБЩЕ GUI: при шаге цены goszakup вызывает функцию Tumar
`EncryptOfferPrice`, и сам Tumar открывает СВОЁ окно, куда оператор вводит цену
(см. TENDER_ECP_SIGNING_GUIDE.md §6.1–6.2: число цены не уходит по websocket, его
вводят в окне). Программно цену не передать — значит автоматизируем это окно.

⚠️ ЭТОТ МОДУЛЬ — СКЕЛЕТ. Точный заголовок окна, имена контролов (поле ввода,
кнопка ОК) и порядок видны только на ЖИВОМ конкурсе — снять на recon (README,
шаг 4) и проставить: GZ_TUMAR_WINDOW_TITLE + селекторы ниже. Подход —
`set_edit_text` (мгновенно, как WM_SETTEXT), а не посимвольный ввод, ради скорости.
"""

from __future__ import annotations

import logging
import time

from . import config

log = logging.getLogger(__name__)

# TODO(recon): уточнить на живом окне Tumar. Кандидаты — снять Inspect.exe/Spy++.
_PRICE_EDIT_AUTOID = ""   # auto_id поля ввода цены (или оставить пустым → по типу)
_OK_BUTTON_TITLE = "OK"   # подпись кнопки подтверждения


def enter_price_and_confirm(price: str, *, timeout: float = 20.0) -> None:
    """Дождаться окна Tumar, вписать цену, подтвердить. Бросает при таймауте.

    pywinauto импортируется лениво — модуль грузится и на Linux/dev без него.
    """
    from pywinauto import Desktop, timings  # noqa: PLC0415 — Windows-only, ленивый импорт

    title = config.TUMAR_PRICE_WINDOW_TITLE
    if not title:
        raise RuntimeError(
            "GZ_TUMAR_WINDOW_TITLE не задан — заголовок окна Tumar надо снять на recon"
        )

    log.info("tumar: ждём окно цены «%s» (до %.0fс)", title, timeout)
    deadline = time.time() + timeout
    win = None
    while time.time() < deadline:
        try:
            win = Desktop(backend="uia").window(title_re=title)
            if win.exists():
                break
        except Exception:  # noqa: BLE001 — окно ещё не появилось, ретраим
            pass
        time.sleep(0.1)
    if win is None or not win.exists():
        raise TimeoutError(f"окно Tumar «{title}» не появилось за {timeout}с")

    timings.wait_until(timeout, 0.1, win.exists)
    win.set_focus()

    # Поле ввода: по auto_id, иначе первый Edit в окне.
    edit = win.child_window(auto_id=_PRICE_EDIT_AUTOID) if _PRICE_EDIT_AUTOID else win.child_window(control_type="Edit")
    edit.set_edit_text(price)  # мгновенная подстановка вместо посимвольной печати
    log.info("tumar: цена вписана, подтверждаем")
    win.child_window(title=_OK_BUTTON_TITLE, control_type="Button").click_input()
