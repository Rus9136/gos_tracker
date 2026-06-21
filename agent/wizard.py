"""Визард подачи заявки в браузере (Playwright) + интеграция с Tumar/NCALayer.

Поток (TENDER_AUTOSUBMIT_PLAN.md §1, разбор HAR): пред-стейдж невозможен, поэтому
`warm_up()` делает всё, что можно ДО open_at (логин по ЭЦП, открыть страницу
объявления, прогреть сессию), а `submit_after_open()` — гонку после открытия:
выбрать лоты → документы → ЗАПЕЧАТАТЬ ЦЕНУ реальным Tumar (нативное окно) →
подписать → preview → финальный POST.

⚠️ СКЕЛЕТ. UI-селекторы (кнопки «Подать», «Подписать цену», поля) и нюансы
NCALayer-логина видны только на ЖИВОМ конкурсе — снять на recon (README шаг 4)
и проставить в местах `TODO(recon)`. Плумбинг (старт браузера, тайминг «выстрела»
через page.request, замер латентности, статусы) — рабочий.

playwright импортируется лениво — модуль грузится и на Linux/dev без него.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from . import config, tumar
from .protocol import RunRequest

log = logging.getLogger(__name__)

StatusCb = Callable[[str, str], None]


class Wizard:
    def __init__(self, req: RunRequest, on_status: StatusCb | None = None):
        self.req = req
        self.prices = {b.lot_id: b.price for b in req.lot_bids}
        self._status = on_status or (lambda s, d: None)
        self._pw = None
        self._browser = None
        self.page = None

    # --- жизненный цикл браузера ---------------------------------------------
    def start(self) -> None:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        self._pw = sync_playwright().start()
        # боевой режим — НЕ headless (NCALayer/Tumar открывают нативные окна).
        self._browser = self._pw.chromium.launch(headless=config.HEADLESS)
        self.page = self._browser.new_context().new_page()

    def close(self) -> None:
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            try:
                if obj:
                    getattr(obj, meth)()
            except Exception:  # noqa: BLE001 — закрытие best-effort
                pass

    # --- фаза 1: прогрев до open_at ------------------------------------------
    def warm_up(self) -> None:
        """Логин по ЭЦП + открыть страницу объявления. Делается ДО open_at."""
        self._status("ARMED", "warm_up: login")
        self._login()
        self._status("ARMED", "warm_up: open announcement")
        # TODO(recon): точный URL страницы объявления/входа в подачу до open_at.
        self.page.goto(f"{config.GOSZAKUP_BASE}/ru/announce/index/{self.req.anno_id}")

    def _login(self) -> None:
        """Вход по ЭЦП: форма /login → NCALayer (выбор ключа + PIN) → пароль портала.

        TODO(recon): автоматизировать всплывающее окно NCALayer выбора ключа и
        ввода PIN — это нативный диалог, управляется pywinauto (ключ из
        req.p12_b64 должен быть импортирован/доступен NCALayer; PIN = req.key_pin).
        Затем шаг пароля портала (req.portal_password) на /user/auth_confirm.
        Детали последовательности — TENDER_ECP_SIGNING_GUIDE.md §2.4.
        """
        self.page.goto(f"{config.GOSZAKUP_BASE}/ru/user/")
        # TODO(recon): клик «войти по ЭЦП» → обработать NCALayer (pywinauto) →
        # ввести req.portal_password в форму auth_confirm.

    # --- фаза 2: гонка после open_at -----------------------------------------
    def submit_after_open(self) -> dict:
        """Полный визард после открытия. Возвращает поля для RunResult."""
        self._status("FIRING", "wizard start")
        app_id = self._open_application()      # «Подать заявку» → черновик appId
        self._select_lots(app_id)
        self._attach_docs(app_id)
        self._seal_prices(app_id)              # ← реальный Tumar, нативное окно
        self._preview(app_id)
        fired_at, latency_ms, body = self._fire(app_id)  # финальный POST
        ok = not (isinstance(body, dict) and str(body.get("status", "")).lower() == "error")
        return {
            "app_id": app_id,
            "status": "SUBMITTED" if ok else "FAILED",
            "fire_latency_ms": latency_ms,
            "fired_at_iso": fired_at.isoformat(),
            "submitted_at_iso": fired_at.isoformat() if ok else None,
            "result": body if isinstance(body, dict) else {"raw": str(body)[:500]},
            "error": None if ok else "server rejected at public_application",
        }

    def _open_application(self) -> int:
        # TODO(recon): дождаться появления кнопки «Подать заявку» (доступна только
        # с open_at), кликнуть, дойти до lots-шага, вытащить appId из URL.
        raise NotImplementedError("recon: кнопка «Подать» и получение appId")

    def _select_lots(self, app_id: int) -> None:
        # POST ajax_add_lots (selectLots[]) → ajax_lots_next (next=1&confirmed=0).
        raise NotImplementedError("recon: выбор лотов")

    def _attach_docs(self, app_id: int) -> None:
        # Документы заранее в библиотеке клиента; здесь прикрепить → ajax_docs_next.
        raise NotImplementedError("recon: документы")

    def _seal_prices(self, app_id: int) -> None:
        """Для каждого лота: открыть шаг цены → Tumar-окно → вписать цену."""
        for lot_id, price in self.prices.items():
            self._status("FIRING", f"seal price lot {lot_id}")
            # TODO(recon): кликнуть UI-элемент «подписать/зашифровать цену» по
            # lot_id — это триггерит ajax_get_encr_info + открытие окна Tumar.
            tumar.enter_price_and_confirm(price)
            # затем JS сам шлёт ajax_add_encrypt + ajax_save_gamma_signs.
        # POST ajax_priceoffers_next.

    def _preview(self, app_id: int) -> None:
        # GET preview/{anno}/{app}. reCAPTCHA, похоже, не enforced (см. §6.x);
        # TODO(recon): если enforced — здесь пред-solve токена.
        raise NotImplementedError("recon: preview")

    def _fire(self, app_id: int):
        """Финальный POST ajax_public_application через тёплую сессию браузера."""
        url = f"{config.GOSZAKUP_BASE}/ru/application/ajax_public_application/{self.req.anno_id}/{app_id}"
        data = {
            "public_app": "Y",
            "agree_price": "true",
            "agree_contract_project": "true",
            "agree_covid19": "false",
        }
        t0 = time.perf_counter()
        resp = self.page.request.post(
            url,
            form=data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        latency_ms = round((time.perf_counter() - t0) * 1000)
        fired_at = datetime.now(UTC)
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 — не-JSON ответ
            body = resp.text()
        log.info("fire #%s: %sms status=%s", self.req.submission_id, latency_ms, resp.status)
        return fired_at, latency_ms, body
