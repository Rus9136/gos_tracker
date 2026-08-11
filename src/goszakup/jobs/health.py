"""Сторож LLM-контура: активный сигнал о молчаливой деградации.

Зачем: правило #7 намеренно гасит ошибки LLM (пайплайн не должен падать из-за
провайдера), поэтому его отказ не всплывает нигде, кроме `log.warning`.
2026-06-30 Cerebras начал отдавать 402 «кончилась квота» — матчинг и
Telegram-уведомления молча встали на 15 дней, пока пользователь не заметил
сам. Здесь мы даём сигнал наружу.

Два независимых индикатора:

1. Живой пинг Cerebras. НЕ выводим здоровье из «давно не было LLM-вызовов»:
   в тихий день новых IT-лотов может не быть вовсе, и такой признак врал бы
   (29.06.2026 — легитимный ноль вызовов). Пинг стоит ~10 токенов и отвечает
   однозначно.
2. Давность последнего матча. Ловит затор ПОСЛЕ LLM, который пингом не видно:
   воркер лежит, или его пул потоков съеден зависшими detail-ретраями. Порог
   щедрый — матчи бывают редкими, ложная тревога здесь хуже пропуска.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import (
    AUTOSUBMIT_AGENT_URL,
    DOCS_DIR,
    GZ_TELEGRAM_BOT_TOKEN,
    PUBLIC_BASE_URL,
)
from ..db.models import User, UserLotMatch
from ..watchlist import watchlist_rules

log = logging.getLogger(__name__)

# Порог давности матча. По умолчанию 48ч, а не 24ч: матчи идут только по
# новым IT-лотам в scope, и сутки тишины бывают штатно (выходные, пустая
# выдача). 0 — выключить проверку.
MATCH_STALE_HOURS = int(os.environ.get("GZ_HEALTH_MATCH_STALE_HOURS", "48"))

# Пауза между повторными алертами. Таймер ежечасный, а сбой длится днями —
# без этого за две недели прилетело бы 300+ сообщений.
ALERT_COOLDOWN_S = int(os.environ.get("GZ_HEALTH_ALERT_COOLDOWN", str(6 * 3600)))

# Порог свободного места под скачиваемые ТЗ. `data/docs/` растёт неограниченно
# (retention гасит только старьё), а переполнение диска рушит скачивание молча.
DISK_MIN_FREE_GB = float(os.environ.get("GZ_HEALTH_DISK_MIN_FREE_GB", "1.0"))

_ALERT_KEY = "goszakup:health:alerted"


def check_redis() -> tuple[bool, str | None]:
    """Пинг Redis. Он держит очередь Dramatiq, rate-limit и счётчики прогонов —
    если он лёг, весь пайплайн молча стоит (актёры не исполняются)."""
    try:
        import redis
    except ImportError:
        return False, "redis-py не установлен"
    url = os.environ.get("GZ_REDIS_URL", "redis://localhost:6379/0")
    try:
        redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3).ping()
    except Exception as e:  # noqa: BLE001 — любой отказ = «недоступен»
        return False, f"{type(e).__name__}: {e}"
    return True, None


def disk_free_gb(path) -> float | None:
    """Свободно ГБ на разделе с документами. None — путь недоступен."""
    try:
        return shutil.disk_usage(str(path)).free / (1024**3)
    except OSError:
        return None

# Срок лицензии Tumar apiKey (из HAR-разведки, TENDER_AUTOSUBMIT_PLAN.md §).
# Крипто-подпись и ГОСТ-шифрование цены в автоподаче завязаны на валидный
# apiKey — истёк = визард агента не сможет сформировать заявку. Пустая строка
# отключает проверку. Проверяется только когда автоподача вообще сконфигурирована.
TUMAR_LICENSE_EXPIRES = os.environ.get("GZ_TUMAR_LICENSE_EXPIRES") or "2026-07-01"
TUMAR_LICENSE_WARN_DAYS = int(os.environ.get("GZ_TUMAR_LICENSE_WARN_DAYS", "14"))


def tumar_license_problem(now: datetime | None = None) -> str | None:
    """Проблема, если лицензия Tumar истекла или истекает скоро; иначе None."""
    if not TUMAR_LICENSE_EXPIRES:
        return None
    try:
        expires = datetime.fromisoformat(TUMAR_LICENSE_EXPIRES)
    except ValueError:
        log.warning("health: не разобрал GZ_TUMAR_LICENSE_EXPIRES=%r", TUMAR_LICENSE_EXPIRES)
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    days_left = (expires - now).total_seconds() / 86400
    if days_left < 0:
        return (
            f"❌ Лицензия Tumar apiKey истекла {TUMAR_LICENSE_EXPIRES} — "
            f"автоподача не сформирует заявку (крипто-подпись/шифрование цены)."
        )
    if days_left <= TUMAR_LICENSE_WARN_DAYS:
        return (
            f"⚠️ Лицензия Tumar apiKey истекает через {days_left:.0f}д "
            f"({TUMAR_LICENSE_EXPIRES}) — продлить до включения автоподачи в бой."
        )
    return None


OWS_TOKEN_WARN_DAYS = int(os.environ.get("GZ_OWS_TOKEN_WARN_DAYS", "14"))


def ows_token_problem(now: datetime | None = None) -> str | None:
    """Токен OWS выдан на год; истёкший маскируется под 404 «Invalid Route»,
    сам по себе он не всплывёт — предупреждаем заранее по дате из env."""
    from ..config import OWS_TOKEN, OWS_TOKEN_EXPIRES

    if not OWS_TOKEN or not OWS_TOKEN_EXPIRES:
        return None
    try:
        expires = datetime.fromisoformat(OWS_TOKEN_EXPIRES)
    except ValueError:
        log.warning("health: не разобрал GZ_OWS_TOKEN_EXPIRES=%r", OWS_TOKEN_EXPIRES)
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    days_left = (expires - now).total_seconds() / 86400
    if days_left < 0:
        return (
            f"❌ Токен OWS API истёк {OWS_TOKEN_EXPIRES} — скрейпинг работает "
            f"на медленном HTML-фолбэке. Запросить новый токен у ЦЭФ."
        )
    if days_left <= OWS_TOKEN_WARN_DAYS:
        return (
            f"⚠️ Токен OWS API истекает через {days_left:.0f}д "
            f"({OWS_TOKEN_EXPIRES}) — запросить продление у ЦЭФ заранее."
        )
    return None


def check_ows_api() -> tuple[bool, str | None]:
    """Дешёвый живой запрос к OWS (справочник, 1 запись). (ok, причина)."""
    from ..api.client import OwsApiError, OwsAuthError, OwsClient
    from ..config import OWS_TOKEN

    if not OWS_TOKEN:
        return True, None  # API не сконфигурирован — HTML-путь, не проблема
    try:
        OwsClient().get_json("/v3/refs/ref_buy_status", params={"limit": 1})
    except OwsAuthError as e:
        return False, f"токен истёк/невалиден: {e}"
    except OwsApiError as e:
        return False, str(e)
    return True, None


def api_degraded_reason() -> str | None:
    """Флаг деградации выставляет FallbackSource при уходе на HTML."""
    try:
        import redis

        from ..sources import API_DEGRADED_KEY

        url = os.environ.get("GZ_REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=3)
        return client.get(API_DEGRADED_KEY)
    except Exception:  # noqa: BLE001 — про Redis скажет check_redis
        return None


def check_llm() -> tuple[bool, str | None]:
    """Минимальный живой вызов Cerebras. (ok, причина отказа)."""
    api_key = os.environ.get("CEREBRAS_API_KEY")
    if not api_key:
        return False, "CEREBRAS_API_KEY не задан"

    try:
        from cerebras.cloud.sdk import Cerebras
    except ImportError:
        return False, "cerebras-cloud-sdk не установлен"

    from ..classify.llm import DEFAULT_MODEL

    model = os.environ.get("GZ_LLM_MODEL", DEFAULT_MODEL)
    try:
        Cerebras(api_key=api_key).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
    except Exception as e:  # noqa: BLE001 — любой отказ провайдера = «болен»
        return False, f"{type(e).__name__}: {e}"
    return True, None


def match_age_hours(session: Session) -> float | None:
    """Часы с последнего посчитанного матча. None — матчей нет вообще."""
    last = session.scalar(select(UserLotMatch.matched_at).order_by(UserLotMatch.matched_at.desc()).limit(1))
    if last is None:
        return None
    # SQLite отдаёт naive (хранит UTC по конвенции), Postgres — aware.
    last = last if last.tzinfo else last.replace(tzinfo=UTC)
    return (datetime.now(UTC) - last).total_seconds() / 3600


class Problem(str):
    """Текст проблемы + стабильный код для дедупа алертов.

    Именно str, а не dataclass: текст уходит в Telegram и в CLI как есть, и
    все вызывающие продолжают работать со списком строк. Дедуп же не может
    ключеваться текстом — в нём плавают числа («94ч назад» → «95ч назад»), и
    ключ менялся бы каждый час, обнуляя cooldown.
    """

    __slots__ = ("code",)

    def __new__(cls, code: str, text: str) -> "Problem":
        obj = super().__new__(cls, text)
        obj.code = code
        return obj


def collect_problems(session: Session) -> list[str]:
    problems: list[Problem] = []

    ok, err = check_llm()
    if not ok:
        problems.append(Problem("llm-down", f"❌ LLM (Cerebras) не отвечает: {err}"))

    ok, err = check_redis()
    if not ok:
        problems.append(Problem(
            "redis-down",
            f"❌ Redis недоступен: {err} — очередь, rate-limit и прогоны стоят",
        ))

    free = disk_free_gb(DOCS_DIR)
    if free is not None and free < DISK_MIN_FREE_GB:
        problems.append(Problem(
            "disk-low",
            f"⚠️ Мало места под документы: {free:.1f} ГБ свободно "
            f"(порог {DISK_MIN_FREE_GB} ГБ) — скачивание ТЗ может встать",
        ))

    # Пустой watchlist — тихая смерть дорогих стадий: ошибок нет, счётчики
    # просто нули (документы не качаются, LLM не зовётся). Случается, когда
    # у всех активных пользователей пустой `categories` и ни у одного
    # запроса нет пре-фильтра.
    if not watchlist_rules(session):
        problems.append(Problem(
            "watchlist-empty",
            "❌ Watchlist пуст: ни одной вертикали у активных пользователей и "
            "ни одного пре-фильтра — документы и LLM выключены для всего рынка",
        ))

    if MATCH_STALE_HOURS > 0:
        age = match_age_hours(session)
        if age is not None and age > MATCH_STALE_HOURS:
            problems.append(Problem(
                "match-stale",
                f"⚠️ Последний матч был {age:.0f}ч назад (порог {MATCH_STALE_HOURS}ч) — "
                f"матчинг мог встать даже при живом LLM.",
            ))

    ok, err = check_ows_api()
    if not ok:
        problems.append(Problem(
            "ows-down",
            f"❌ OWS API не отвечает ({err}) — скрейпинг на медленном HTML-фолбэке",
        ))

    token_problem = ows_token_problem()
    if token_problem:
        problems.append(Problem("ows-token", token_problem))

    degraded = api_degraded_reason()
    if degraded:
        problems.append(Problem(
            "api-degraded",
            f"⚠️ Источник деградировал на HTML-фолбэк (последняя причина: {degraded})",
        ))

    # Лицензию Tumar проверяем только когда автоподача сконфигурирована — иначе
    # на инстансе без автоподачи это ложный шум.
    if AUTOSUBMIT_AGENT_URL:
        tumar = tumar_license_problem()
        if tumar:
            problems.append(Problem("tumar-license", tumar))

    return problems


def _alertable(problems: list[str]) -> list[str]:
    """Отсеять проблемы, о которых уже сообщали в текущем окне cooldown.

    SET NX EX на КАЖДЫЙ код отдельно, а не один ключ на весь прогон: 2026-08-11
    общий ключ занял алерт про `llm-down`, и всплывшая следом независимая
    `match-stale` попала под чужой cooldown и не ушла вовсе. Своё окно у
    каждого вида проблемы: новая пробивает тишину сразу, повтор той же молчит.

    Если Redis недоступен — шлём всё. Лучше лишнее сообщение, чем тишина:
    именно тишина и стоила нам двух недель.
    """
    try:
        import redis

        url = os.environ.get("GZ_REDIS_URL", "redis://localhost:6379/0")
        client = redis.Redis.from_url(url, decode_responses=True)
    except Exception as e:  # noqa: BLE001
        log.warning("health: дедуп через Redis недоступен (%s) — шлём алерт", e)
        return problems

    fresh: list[str] = []
    for p in problems:
        # getattr — на случай обычной строки: без кода дедупить нечем, и
        # умолчание «пропустить» безопаснее умолчания «замолчать».
        code = getattr(p, "code", None)
        if code is None:
            fresh.append(p)
            continue
        try:
            if client.set(f"{_ALERT_KEY}:{code}", "1", nx=True, ex=ALERT_COOLDOWN_S):
                fresh.append(p)
        except Exception as e:  # noqa: BLE001
            log.warning("health: дедуп %s не сработал (%s) — шлём", code, e)
            fresh.append(p)
    return fresh


def _admin_chat_ids(session: Session) -> list[str]:
    # notify_telegram здесь НЕ смотрим: этот флаг про уведомления о лотах,
    # а поломка сервиса — операционный сигнал, его гасить отдельно.
    rows = session.scalars(
        select(User).where(User.is_admin.is_(True), User.telegram_chat_id.isnot(None))
    ).all()
    return [u.telegram_chat_id for u in rows if u.telegram_chat_id]


def run_health_check(session: Session, *, notify: bool = True) -> list[str]:
    """Возвращает список проблем (пустой = здоров). notify=False — только проверить."""
    problems = collect_problems(session)
    if not problems:
        log.info("health: ок")
        return []

    for p in problems:
        log.warning("health: %s", p)

    if not notify:
        return problems

    if not GZ_TELEGRAM_BOT_TOKEN:
        log.warning("health: GZ_TELEGRAM_BOT_TOKEN не задан — алерт некуда слать")
        return problems

    # Получателей ищем ДО дедупа: иначе ключи cooldown встали бы на проблему,
    # о которой никому не сообщили, и она замолчала бы на все 6 часов зря.
    chat_ids = _admin_chat_ids(session)
    if not chat_ids:
        log.warning("health: нет админов с telegram_chat_id — алерт некуда слать")
        return problems

    fresh = _alertable(problems)
    if not fresh:
        log.info("health: алерт подавлен (cooldown %dс)", ALERT_COOLDOWN_S)
        return problems

    from ..notify.telegram import send_message

    text = "<b>goszakup: проблема</b>\n\n" + "\n".join(fresh) + f"\n\n{PUBLIC_BASE_URL}/expenses"
    for chat_id in chat_ids:
        ok, err = send_message(chat_id, text)
        if not ok:
            log.warning("health: не отправился алерт в %s: %s", chat_id, err)

    return problems
