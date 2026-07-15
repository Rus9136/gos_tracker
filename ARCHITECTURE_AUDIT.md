# Архитектурный аудит gos_tracker

Приёмочный аудит перед масштабированием на ~700–800 тыс. лотов/год и включением
автоподачи заявок в бой. Нумерация находок (P0 №N) — единая для всего документа
и коммитов ремедиации. Статусы: **FIXED** (ссылка на коммит) / **REFUTED** /
**DEFERRED** / **OPEN** (не бралось в эту сессию).

---

## Карта архитектуры

**Слои (граф ацикличен):** `cli.py`/`web/app.py`/`queue/actors.py` →
`jobs/` → `classify/` + `scraper/` → `db/`. `scope.py` — общий read-time фильтр
для web и matcher-fan-out. Автоподача — отдельный контур: `queue/autosubmit.py`
(таймер) → `autosubmit/scheduler.py` → `autosubmit/agent_client.py` →
Windows submit-agent (`agent/`, отдельный деплой) → `POST /autosubmit/result`.

**Пайплайн:** Dramatiq `daily → listing → detail → analyze → match → notify`
(7 очередей `goszakup_*`), Redis неперсистентный (broker + rate-limit +
pending-счётчик прогона). БД: Postgres 15 прод / SQLite WAL dev.

**Внешние интеграции:** goszakup.gov.kz (+ v3bl через SOCKS), Cerebras (LLM),
Telegram Bot API, NCALayer/Tumar (только Windows-агент), Redis, Sentry (опц.).

---

## Этап 0 — Верификация спорных утверждений

| # | Утверждение | Вердикт |
|---|---|---|
| 1 | `deadline_guard` — мёртвый код, в `agent/timing.py` нет | **CONFIRMED** — был только в `autosubmit/timing.py:63`, без вызовов; в `agent/timing.py` отсутствовал |
| 2 | IntegrityError на flush рвёт весь Phase-1 листинг | **CONFIRMED** — единый `try` вокруг цикла (`run_preset.py:390-412`), rollback всей сессии, savepoint'ов нет |
| 3 | Неизвестный статус → `status_code=None` → `is_actual=False` без warning | **CONFIRMED** — `_status_code_from_name` (`run_preset.py:336-344`) точное сравнение, иначе None; без лога/fallback |
| 4 | Лицензия Tumar истекает 2026-07-01 | **CONFIRMED** — источник `TENDER_AUTOSUBMIT_PLAN.md:140` («из HAR») |
| 5 | CSRF-защита | **NEW FINDING** — токенов нет, защита только неявная через дефолт `SameSite=lax`; `https_only` не задан. Гейт 3, не Гейт 1 |
| 6 | Telegram-вебхук проверяет secret token | **CONFIRMED (защищено)** — `hmac.compare_digest`, fail-closed 503, CLI ставит `secret_token`. Не фиксим |
| 7 | Утечка секретов vault в log/raise | **NEW FINDING → FIXED** — прямых логов нет, но Sentry `send_default_pii=True` + захват локальных сериализовал бы `repr()` секретных dataclass'ов |
| 8 | XSS через `\|safe`/`Markup`/`autoescape off` | **CONFIRMED (чисто)** — 0 совпадений, Jinja2 autoescape по умолчанию. Не фиксим |

---

## Статус устранения (эта сессия)

### Гейт 1 — автоподача (закрыт)

| Находка | Статус | Коммит |
|---|---|---|
| P0-2 очередь `goszakup_autosubmit` не читается воркером | **FIXED** | `85b812c` |
| P0-3a двойной dispatch (нет claim до отправки) | **FIXED** | `5e2fc3b` |
| P0-3b агент не дедуплицирует `/run` | **FIXED** | `a4b5650` |
| P0-3c `fire()` слепой ретрай после отправки | **FIXED** | `67f0c06` |
| P0-apply `apply_result` затирал терминальный статус | **FIXED** | `2bc2fd0` |
| P0-channel fail-open канал Linux↔agent | **FIXED** | `eb7aebf` |
| P0/P1-5 `deadline_guard` мёртв на обеих сторонах | **FIXED** | `26e2918` |
| P1 срок лицензии Tumar не проверяется | **FIXED** | `1cba967` |
| verify-7 секреты автоподачи утекают в Sentry | **FIXED** | `9013a80` |
| P0-9 нет тестов `vault/` | **FIXED** | `6b057c8` |

### Быстрые фиксы (вне гейтов)

| Находка | Статус | Коммит |
|---|---|---|
| P0-1 insecure default `SECRET_KEY` (обход auth при сбое конфига) | **FIXED** | `41e0dfa` |
| P0-8 IDOR `/document/{id}/download` без scope | **FIXED** | `e54ac6e` |

### DEFERRED (в рамках Гейта 1)
- **fire() reconcile вместо UNKNOWN** — требует эндпоинта goszakup «заявка
  принята?», которого нет в recon. Реализован фаллбэк (UNKNOWN + алерт);
  reconcile — когда появятся данные об API.

### OPEN — Гейт 2 (масштаб) и Гейт 3 (расширение пользователей)

Не бралось в эту сессию (по заданию — только верификация + 2 быстрых фикса +
Гейт 1). Остаётся к работе:

**Гейт 2 (до 800K):** составные индексы под `/actual` и `/matched`; `/matched`
без `LIMIT` в SQL; `init_db.create_all` конфликтует с Alembic; гонка get-then-insert
в `_upsert_lot_from_listing` (P0 №4) и `_get_or_create_org`; retention `data/docs/`;
TTL-мьютекс rate-limit (5с < длительности запроса); потеря лотов при прерванном
листинге; `_status_code_from_name`→None молча гасит лот (P0 №6); тихая деградация
листинг-парсера (P0 №7); нет ретрая на не-429 ошибки LLM; health-сторож слеп к
Redis/воркеру/диску/backlog; `/expenses` тянет все `LlmCall` в Python; блокирующий
`httpx.post` в async `telegram_webhook`.

**Гейт 3 (безопасность/UX):** IDOR `/organization`, `/lot/chat`, `/star`;
prompt-injection через текст ТЗ в чат; CSRF-токены + `https_only`/`SameSite`
(verify #5); tooltip дедлайна показывает UTC вместо Алматы; Sentry PII/cookie.

**Техдолг:** монолит `web/app.py` (1897 строк); импорт приватных `jobs._save_*`
в web; расхождения доков (README про Claude/SQLite, ROADMAP без autosubmit);
`*.har` с PII в рабочем каталоге; мёртвые поля моделей; 6 предсуществующих
ruff-замечаний в `classify/llm.py`, `scraper/announce.py`, `tests/conftest.py`,
`tests/test_rules.py`, `tests/test_simhash.py` (не трогались — вне скоупа сессии).

---

## Приложение: полный список находок исходного аудита

Полный текст находок P0/P1/P2/P3 с обоснованиями `файл:строка` — в истории
план-файла сессии аудита; сводка по гейтам выше отражает их приоритизацию.
