"""Windows submit-agent для автоподачи заявок goszakup (Phase 2).

Отдельный самодостаточный деплой на Windows-узле с реальным Tumar CSP +
NCALayer (см. TENDER_AUTOSUBMIT_PLAN.md, приложение «организация Windows-узла»).
Получает от Linux-планировщика задание (`POST /run` с RunRequest), прогревается
до `open_at`, исполняет визард подачи с реальным Tumar (запечатывание цены),
выстреливает финальный POST и отчитывается обратно на Linux
(`POST /autosubmit/result`).

НЕ зависит от пакета `goszakup`: своя лёгкая копия протокола (`protocol.py`) и
тайминга (`timing.py`), зависимости — только httpx + playwright + pywinauto.
"""
