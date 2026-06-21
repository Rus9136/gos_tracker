"""Recon-утилита: вывести заголовок и контролы открытого нативного окна.

Запускать НА WINDOWS, когда нужное окно ОТКРЫТО:
- окно ввода цены Tumar (на шаге priceoffers), или
- окно NCALayer (выбор ключа / ввод PIN при входе).

Результат отдать мне — я заполню `tumar.py`/`wizard.py` и `.env`
(`GZ_TUMAR_WINDOW_TITLE`). pywinauto импортируется лениво.

Использование:
  python -m agent.recon_dump                 # список всех окон верхнего уровня
  python -m agent.recon_dump "Tumar"         # дамп контролов окна по подстроке
  python -m agent.recon_dump "NCALayer"
"""

from __future__ import annotations

import sys


def list_windows() -> None:
    from pywinauto import Desktop  # noqa: PLC0415 — Windows-only, ленивый импорт

    print("Видимые окна верхнего уровня (подстроку заголовка передать вторым аргументом):")
    for w in Desktop(backend="uia").windows():
        try:
            t = w.window_text()
            if t and t.strip():
                print("  ", repr(t))
        except Exception:  # noqa: BLE001 — недоступное окно пропускаем
            pass


def dump(title_re: str) -> None:
    from pywinauto import Desktop  # noqa: PLC0415

    win = Desktop(backend="uia").window(title_re=title_re)
    win.wait("exists ready", timeout=10)
    print("=== TITLE ===", repr(win.window_text()))
    print("=== CONTROLS (auto_id / control_type / текст — это нужно мне) ===")
    win.print_control_identifiers()


def main() -> None:
    if len(sys.argv) < 2:
        list_windows()
    else:
        dump(sys.argv[1])


if __name__ == "__main__":
    main()
