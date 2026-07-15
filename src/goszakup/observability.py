"""Sentry init. No-op без `SENTRY_DSN` — на дев-машине ничего не делает.

Вызывать из точек входа: web/app.py (uvicorn), cli.py (typer), jobs/daily.py.
Идемпотентно: повторные вызовы безопасны (sentry-sdk сам ловит double-init).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_initialised = False

# Ключи секретов автоподачи — вычищаем из Sentry-события, включая локальные
# переменные в стеке (verify-7). Defence-in-depth поверх repr=False на
# dataclass'ах RunRequest/DecryptedCredential/LotBid: send_default_pii=True не
# отключает явно переданный EventScrubber.
SECRET_DENYLIST_EXTRA = [
    "p12",
    "p12_b64",
    "p12_bytes",
    "p12_enc",
    "portal_password",
    "portal_password_enc",
    "key_pin",
    "key_pin_enc",
    "pin",
    "price",
    "bid",
    "bid_enc",
    "bid_nonce",
]


def setup_sentry(component: str) -> None:
    """`component` — 'web' / 'cli' / 'daily', попадает в тег для фильтрации в UI."""
    global _initialised
    if _initialised:
        return

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        # Дев-окружение или прод без подключенного Sentry — молча.
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
    except ImportError:
        log.warning("sentry-sdk не установлен — пропускаю инициализацию")
        return

    # WARNING+ — события, INFO+ — breadcrumbs. Уровни ниже WARNING не шлём,
    # иначе Cerebras-ретраи (log.info) забьют квоту.
    logging_integration = LoggingIntegration(level=logging.INFO, event_level=logging.WARNING)

    environment = os.environ.get("SENTRY_ENVIRONMENT", "production")
    release = os.environ.get("SENTRY_RELEASE")  # можно прокинуть git SHA на деплое

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        integrations=[logging_integration],
        # Traces — пока не нужны, дороже и обычный issue tracking хватит.
        traces_sample_rate=0.0,
        # PII — урлы и заголовки могут содержать БИН/контакты заказчиков,
        # но не пользовательский PII. Включаем — это упрощает диагностику.
        send_default_pii=True,
        # Но секреты автоподачи (p12/пароль/PIN/цена) вычищаем явно — из полей
        # события И из локальных переменных в стеке (verify-7).
        event_scrubber=EventScrubber(
            denylist=DEFAULT_DENYLIST + SECRET_DENYLIST_EXTRA, recursive=True
        ),
    )
    sentry_sdk.set_tag("component", component)
    _initialised = True
    log.info("Sentry initialised: env=%s component=%s", environment, component)
