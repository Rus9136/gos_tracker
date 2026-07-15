"""«Выстрел» — финальный POST подачи заявки по тёплой сессии.

Из разбора HAR: финальная подача — крошечный (~200 б) POST без CSRF-токена,
авторизация только session-cookie. Поэтому его можно отправить сырым httpx за
<100 мс — не нужен браузер. Источник cookie — прогретая сессия submit-agent'а
(или переданный cookie-jar). Это последний шаг визарда; всё до него (включая
шифрование цены реальным Tumar) выполняется агентом и в этот модуль не входит.

`fire()` синхронный и тонкий специально: меньше слоёв = меньше латентность в
критпути гонки. Один быстрый ретрай на сетевую ошибку (но не на бизнес-ошибку
сервера — её отдаём наверх как есть).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

V3BL_BASE_URL = "https://v3bl.goszakup.gov.kz"


@dataclass(frozen=True)
class FireResult:
    ok: bool
    status_code: int
    latency_ms: int
    body: dict | str | None
    error: str | None = None
    # unknown=True: POST ушёл, но ответ не получен (ReadTimeout/обрыв ПОСЛЕ
    # отправки). Сервер мог принять заявку — состояние неизвестно, повторять
    # НЕЛЬЗЯ (задвоит подачу). Наверх уходит как статус UNKNOWN + алерт.
    unknown: bool = False


def _is_success(body: dict | str | None) -> bool:
    # Сервер на ошибке возвращает {"status":"error","message":...}; на успехе —
    # иной JSON/редирект-маркер. Консервативно: ошибка только при явном error.
    if isinstance(body, dict):
        return str(body.get("status", "")).lower() != "error"
    return True


def fire(
    anno_id: int,
    app_id: int,
    cookies: dict[str, str],
    *,
    agree_price: bool = True,
    agree_contract_project: bool = True,
    agree_covid19: bool = False,
    recaptcha_token: str | None = None,
    base_url: str = V3BL_BASE_URL,
    timeout: float = 5.0,
    retries: int = 1,
) -> FireResult:
    """POST ajax_public_application — принять заявку. Возвращает FireResult с латентностью."""
    url = f"{base_url}/ru/application/ajax_public_application/{anno_id}/{app_id}"
    data = {
        "public_app": "Y",
        "agree_price": "true" if agree_price else "false",
        "agree_contract_project": "true" if agree_contract_project else "false",
        "agree_covid19": "true" if agree_covid19 else "false",
    }
    # reCAPTCHA на этом шаге, похоже, не enforced (в успешной HAR токена не было),
    # но если recon-2 покажет обратное — поле прокидывается заранее пред-solved'ом.
    if recaptcha_token:
        data["g-recaptcha-response"] = recaptcha_token

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": f"{base_url}/ru/application/preview/{anno_id}/{app_id}",
    }

    last_err: str | None = None
    with httpx.Client(timeout=timeout, cookies=cookies, http2=True) as client:
        for _attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                resp = client.post(url, data=data, headers=headers)
                latency_ms = round((time.perf_counter() - t0) * 1000)
                try:
                    body: dict | str = resp.json()
                except ValueError:
                    body = resp.text
                ok = resp.status_code < 400 and _is_success(body)
                return FireResult(
                    ok=ok,
                    status_code=resp.status_code,
                    latency_ms=latency_ms,
                    body=body,
                    error=None if ok else "server rejected",
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                # Соединение не установлено → запрос гарантированно НЕ ушёл,
                # повтор безопасен (не задвоит подачу).
                last_err = f"{type(e).__name__}: {e}"
                continue
            except httpx.HTTPError as e:
                # ReadTimeout/обрыв ПОСЛЕ отправки: сервер мог уже принять заявку.
                # Слепой ретрай задвоил бы подачу — НЕ ретраим, помечаем UNKNOWN.
                return FireResult(
                    ok=False,
                    status_code=0,
                    latency_ms=0,
                    body=None,
                    unknown=True,
                    error=f"UNKNOWN: {type(e).__name__}: {e}",
                )
    return FireResult(ok=False, status_code=0, latency_ms=0, body=None, error=last_err)
