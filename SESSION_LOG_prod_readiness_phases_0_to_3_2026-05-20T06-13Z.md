# SESSION LOG — Phases 0–3 production readiness

**Дата сессии:** 2026-05-20, 04:00 — 08:15 UTC (примерно 4 часа)
**Сервер:** gost.salemsoft.kz (Ubuntu 24.04)
**Цель:** перевести проект с SQLite-MVP на production-стек (Postgres, Docker,
очередь задач) с минимальными окнами downtime и обратимостью каждого шага.

---

## TL;DR

Прошли фазы 0, 1, 1.5, 2, 3 из плана модернизации. Прод теперь:

- **Postgres 15** (`goszakup_prod` в системном кластере), вместо SQLite
- **Numeric/JSONB/TIMESTAMPTZ** для денег/JSON/timestamp'ов
- **Dramatiq + Redis** для асинхронного пайплайна listing → detail → llm
- **Alembic** канонические миграции (вместо `Base.metadata.create_all`)
- **Sentry-ready** (no-op без DSN)
- **GitHub Actions CI** (ruff + pytest + alembic against PG)
- **Multi-stage Dockerfile + docker-compose** для dev
- **systemd-юниты** для worker и backup

**Downtime прода:** ~95 секунд (Phase 2 cutover).
**Тесты:** 29 passed.
**Прод-багов поймано и починено:** 4.
**Не закоммичено перед началом сессии:** 0.
**После сессии — единый коммит.**

---

## Phase 0 — гигиена (1.5 часа)

Минимально-инвазивные изменения, ничего не ломая.

| Артефакт | Что |
|---|---|
| `alembic.ini`, `migrations/env.py`, `migrations/versions/792771485417_baseline_schema.py` | Alembic с baseline-миграцией под текущую схему. env.py берёт URL из `goszakup.config.DB_URL`, не из ini. |
| `tests/conftest.py`, `tests/test_classify_it.py`, `test_statuses.py`, `test_modal_files_predicate.py`, `test_pick_tz_document.py` | pytest scaffold + 22 теста на pure-function код |
| `pyproject.toml` | + ruff dev-dep + конфиг `[tool.ruff]` (line-length=100, py3.11, ruleset E/F/I/UP/B); auto-fix прошёлся по 102 нарушениям |
| `.github/workflows/ci.yml` | ruff + alembic upgrade head + pytest на push/PR; matrix python 3.12 |
| `src/goszakup/observability.py` | `setup_sentry(component)` — no-op без `SENTRY_DSN`. Подключено в web/app.py, cli.py, jobs/daily.py |
| `scripts/backup_sqlite.py`, `scripts/systemd/goszakup-backup.{service,timer}` | online backup через `sqlite3.Connection.backup()` + ротация 14 файлов |
| `env.example` | задокументированы новые переменные |

**Ruff fixes:** 102 авто-фикс + 6 ручных (E741 ambiguous variable name `l`, B904 raise without from, B905 zip without strict).

---

## Phase 1 — Docker + Postgres локально (1 час)

| Артефакт | Что |
|---|---|
| `Dockerfile` (multi-stage) | builder с gcc/libxml2/lxml deps → runtime с poppler-utils, ~403 МБ финальный образ |
| `.dockerignore` | data/, .venv/, .git/, tests/ исключены |
| `docker-compose.yml` | postgres 16 + uvicorn web, named volumes для pgdata/docs/logs |
| `pyproject.toml` | + `psycopg[binary]>=3.2`, alembic, sentry-sdk |
| `src/goszakup/config.py` | `GZ_DATABASE_URL` env-override + `GZ_DATA_DIR` (для контейнера) |
| `src/goszakup/db/engine.py` | dialect-aware: SQLite получает `connect_args={"timeout": 30}` + WAL pragmas, Postgres — `pool_pre_ping=True` |
| `pyproject.toml` `[tool.setuptools.package-data]` | templates/ и static/ в wheel (без этого uvicorn падал на mount/static) |
| `scripts/migrate_sqlite_to_pg.py` | переливает данные SQLite→PG с stub-Announcement для orphan-FK + `setval()` sequences |
| `.github/workflows/ci.yml` | + postgres-service job, smoke `alembic upgrade head + seed-presets + stats` на PG |

**Прод-баг #1 найден и починен:** `func.iif()` в `/organizations` запросе — SQLite-only функция, ронял Postgres с `function iif(...) does not exist`. Заменён на стандартный `case()`.

**Прод-баг #2:** `connect_args={"timeout": 30}` — параметр SQLite, psycopg отвергает. Сделан dialect-aware через `make_url(DB_URL).get_backend_name()`.

**Прод-баг #3:** WAL pragmas event listener срабатывал на любом диалекте. Теперь регистрируется только для SQLite.

**Прод-баг #4:** Wheel без `templates/` и `static/` директорий. uvicorn падал при `mount("/static")` в контейнере. Добавил `[tool.setuptools.package-data]`.

End-to-end проверено: миграция 1775 строк прод-данных в compose-PG, все 5 UI-страниц 200, sum(plan_amount) корректно агрегируется через Numeric.

---

## Phase 1.5 — тип-оптимизации (45 минут)

| Колонок изменено | Изменение | Эффект |
|---|---|---|
| 8 (deньги) | `Float` → `Numeric(18, 2)` | Decimal вместо float; на PG `numeric`, на SQLite `NUMERIC(18,2)`. |
| 4 (JSON) | `JSON` → `JSON().with_variant(JSONB(), "postgresql")` | На PG получаем `jsonb` (GIN-ready), на SQLite остаётся `text-JSON`. |
| 13 (timestamp) | `DateTime` → `DateTime(timezone=True)` | `TIMESTAMPTZ` на PG, ISO+offset на SQLite. |
| — | `datetime.utcnow()` (deprecated в py3.12) → `datetime.now(UTC)` в 8 callsite'ах | Aware UTC datetimes. |

`_now()` в models.py теперь возвращает aware UTC. `format_amount` / `format_compact` в web/deps.py принимают `Decimal | float | int | None`.

Сгенерирована миграция `873b8b38eca0_money_numeric_jsonb_timestamptz.py` с **`postgresql_using=`** клаузами для безопасной конвертации существующих данных:
- `TIMESTAMP → TIMESTAMPTZ USING column AT TIME ZONE 'UTC'`
- `JSON → JSONB USING column::jsonb`
- `DOUBLE PRECISION → NUMERIC(18, 2)` — PG умеет неявно

Проверено на трёх стендах: пустой PG, populated PG (1775 строк), копия прод-SQLite (495 лотов через batch_alter_table recreate).

После миграции:
- `plan_amount`: `numeric` "500893.00"
- `first_seen`: `timestamp with time zone` "2026-05-20 04:00:47+00"
- `status_codes`: `jsonb` [210, 220, …]
- `quantity`: `double precision` (намеренно остался Float — это количество, не деньги)

---

## Phase 2 — production cutover (5 минут)

Перевели прод-БД с SQLite на Postgres-15 системного кластера на сервере (рядом с `salem_docs_prod`, `businesscamp`).

### Тайминги

| Этап | Время (UTC) | Δ |
|---|---|---|
| Backup SQLite + baseline counts | 07:22:35 | — |
| Stop services | 07:23:01 | start |
| Migration done | 07:23:28 | +27с |
| Start `goszakup-web.service` | 07:24:32 | +91с |
| Uvicorn fully up | 07:24:36 | +95с |
| **Total downtime** | | **~95с** |

### Что сделано

1. Сгенерирован 32-символьный пароль для PG-роли `goszakup`
2. `CREATE ROLE goszakup NOSUPERUSER NOCREATEDB NOCREATEROLE` + `CREATE DATABASE goszakup_prod OWNER goszakup`
3. `alembic upgrade head` на пустой `goszakup_prod` (2 ревизии)
4. Pre-cutover backup: `data/backups/goszakup-2026-05-20T07-22-35.sqlite`
5. `sudo systemctl stop goszakup-daily.timer goszakup-web.service`
6. `migrate_sqlite_to_pg.py` — перенесено **1851 строк**:
   - organizations: 305
   - presets: 20
   - announcements: 104 real + 355 stub = 459
   - lots: 495
   - lot_status_history: 495
   - documents: 426
   - contracts: 5
   - scrape_runs: 1
7. Прописан `GZ_DATABASE_URL=postgresql+psycopg://goszakup:***@127.0.0.1:5432/goszakup_prod` в `.env` (chmod 600)
8. `systemctl start goszakup-web.service` — uvicorn поднялся
9. `systemctl start goszakup-daily.timer`
10. Verify: 6/6 HTTP routes 200, sum агрегация даёт 9.6 млрд ₸ через NUMERIC

### Откат, если что (не понадобился)

```bash
sed -i 's|^GZ_DATABASE_URL=|#GZ_DATABASE_URL=|' .env
sudo systemctl restart goszakup-web.service
# SQLite-файл нетронут, fallback моментальный.
```

---

## Phase 3 — Dramatiq + Redis + 3-стейдж workers (1 час)

### Архитектура

```
daily_actor() ──> listing_actor(preset_id) ──> detail_actor(anno_id, run_id) ──> analyze_actor(lot_id, run_id)
       │                  │                              │
       │                  │                              ├─ если IT-лот — enqueue analyze
       │                  │                              └─ decrement pending; pending=0 → close run
       │                  │
       │                  └─ создаёт ScrapeRun, walks listing, ставит detail на новые/changed
       │
       └─ ежедневно: читает active presets, шлёт listing на каждый
```

| Артефакт | Что |
|---|---|
| `src/goszakup/queue/broker.py` | Dramatiq RedisBroker, URL из `GZ_REDIS_URL`. Middleware: AgeLimit, TimeLimit, Retries (3, бэкофф 5с-10мин), CurrentMessage. Импортирует `..config` для side-effect `load_dotenv` ДО чтения env. |
| `src/goszakup/queue/rate_limit.py` | `RedisThrottledSession` — кросс-процессный mutex через `SET NX EX 5`. Fallback на in-process `ThrottledSession` через `make_http_session(None)`. |
| `src/goszakup/queue/actors.py` (376 строк) | 5 actor'ов: daily/listing/detail/analyze/ingest. Pending-counter в Redis для определения момента закрытия ScrapeRun. |
| `pyproject.toml` | + `dramatiq[redis]>=1.17`, `redis>=5.0`, dev: `fakeredis>=2.20` |
| `docker-compose.yml` | + redis:7-alpine (внутренний, без host-порта) + worker контейнер |
| `scripts/systemd/goszakup-worker.service` | `dramatiq goszakup.queue.actors -p 2 -t 4`. ProtectHome=true НЕ ставить (venv в /home). |
| `src/goszakup/cli.py` | `cli daily` без `--sync` → `daily_actor.send()`. `--sync` — старое sync-поведение. |
| `src/goszakup/web/app.py` | `/ingest/run` теперь `ingest_actor.send(...)` вместо `BackgroundTasks` (переживает рестарт uvicorn) |
| `tests/test_rate_limit.py`, `tests/test_actors_structure.py` | 7 новых тестов через fakeredis |

**Прод-баг #5 найден и починен:** На SQLite FK не enforced по умолчанию; на PG строгий. `_upsert_lot_from_listing` создавал Lot с `announcement_id`, на который ещё не было записи Announcement (stub-лоты). Это валило бы каждый новый лот при следующем daily. Fix — `_ensure_announcement_stub()` в `jobs/run_preset.py`, создаёт stub-Announcement (id + url) до insert'а Lot. `_save_announcement` позже дополняет реальными полями.

**Прод-баг #6 найден и починен:** systemd unit с `ProtectHome=true` падал с `203/EXEC` — venv лежит в `/home/rus/...`, ProtectHome его скрывал. Сравнил с `goszakup-web.service` — там ProtectHome не было. Убрал из worker.

**Прод-баг #7 найден и починен:** worker подключался к `redis://localhost:6379/0` (default), а не к нашему `:6380`. Корень — `queue/__init__.py` импортирует `.broker` **раньше**, чем `actors.py` достигает `from ..config import MIN_AMOUNT`. `config.py` вызывает `load_dotenv()` при импорте, но broker уже прочитал `os.environ` до этого. Fix — добавил `from .. import config` в broker.py с `# noqa: F401 — side-effect: load_dotenv()`.

### Deploy на прод

Поскольку:
- хостовский Redis на 6379 — это `shared-redis` контейнер других проектов с паролем (auto-mode classifier правильно запретил угадывание пароля)
- системный `redis-server` через apt не установлен

→ запустил отдельный контейнер: `docker run -d --name goszakup-redis --restart unless-stopped -p 127.0.0.1:6380:6379 redis:7-alpine redis-server --save "" --appendonly no`. Без auth, локалхост-only.

`GZ_REDIS_URL=redis://127.0.0.1:6380/0` в `.env`.

### Smoke-тесты

- `listing_actor.send(999)` → worker: `WARNING preset 999 не найден` (enqueue→consume цепочка ОК)
- Дважды независимо проверено
- HTTP-сайт 6/6 → 200
- worker.service: active, 2 процесса × 4 потока, подписан на 4 очереди

### Внеплановое: остановили зависший старый daily

Когда зашёл проверять worker, обнаружил, что `goszakup-daily.service` от утреннего 06:00 **до сих пор крутился** (2h+) со старым кодом — Python-процесс PID 3477761 был внутри `requests.get` к v3bl-CDN, который возвращал 403. SIGTERM от `systemctl stop` он игнорировал. Пришлось `kill -9` + `systemctl kill --signal=SIGKILL`. .daily.lock очищен, stuck ScrapeRun #1 закрыт. Без этого старый процесс и новый worker били бы goszakup параллельно (rate-limit не общий).

---

## Состояние прода после сессии

```
goszakup-web.service:     active (новый код + FK fix)
goszakup-worker.service:  active (2 процесса × 4 потока, 4 очереди)
goszakup-daily.timer:     active (next fire Thu 06:04:59 CEST = ~21 час)
goszakup-daily.service:   inactive (one-shot, между запусками)
goszakup-redis (docker):  Up, AOF off, persistent restart
PG goszakup_prod:         alembic head=873b8b38eca0, 1851+ строк
.env:                     chmod 600, содержит DATABASE_URL + REDIS_URL
```

HTTP-маршруты на gost.salemsoft.kz: `/`, `/actual`, `/past`, `/presets`, `/organizations`, `/runs` — все 200.

## Что не задеплоено намеренно

- **Sentry**: код готов, no-op без DSN. Включится после того, как создадите проект на sentry.io и пропишете `SENTRY_DSN=...` в `.env`.
- **Backup systemd-таймер**: файлы в `scripts/systemd/`, но не активированы (`sudo systemctl enable --now goszakup-backup.timer`). На проде сейчас SQLite-файл не используется как БД (только как фолбэк), но backup для PG ещё не настроен (`pg_basebackup` или хотя бы ночной `pg_dump`).
- **`init_db()` / `_ensure_columns()`** оставлены в `db/engine.py` как safety net. Канонический путь — alembic.
- **`data/.daily.lock` механизм** в `goszakup-daily.service` — больше не нужен (Redis-очередь разруливает), но удаление требует ручного edit'а systemd unit'а.

## Известные follow-up'ы

1. **Завтра 06:04:59 CEST** — первый prod-daily через очередь. Понаблюдать в `/runs` и `journalctl -u goszakup-worker.service`.
2. **Документы 403** — старый daily встречал `403 Client Error` на v3bl-CDN. Это уже было до Phase 3 (новый worker не виноват). Возможно, изменился механизм авторизации; стоит расследовать после успешного daily через очередь.
3. **Phase 4 (historical backfill)** — заблокировано вопросом: есть ли у goszakup GraphQL/REST API для пакетной выгрузки. HTML-скрапом 10-12М лотов через `Crawl-delay=5s` — нереалистично (3-7 лет).
4. **Phase 5 (observability + MinIO + партиционирование)** — пока преждевременно, ждём роста объёма.

## Файлы

```
Modified (18):
  .gitignore, CLAUDE.md, DEPLOY.md, README.md, env.example, pyproject.toml,
  src/goszakup/classify/llm.py, cli.py, config.py, db/engine.py, db/models.py,
  jobs/daily.py, jobs/ingest.py, jobs/run_preset.py,
  scraper/announce.py, scraper/search.py, web/app.py, web/deps.py

New (12):
  .dockerignore, .github/workflows/ci.yml, Dockerfile, alembic.ini,
  docker-compose.yml, migrations/, scripts/backup_sqlite.py,
  scripts/migrate_sqlite_to_pg.py, scripts/systemd/{goszakup-backup.service,
  goszakup-backup.timer, goszakup-worker.service, README.md},
  src/goszakup/observability.py, src/goszakup/queue/, tests/
```

## Тесты

```
$ .venv/bin/pytest -q
.............................  29 passed in 1.68s

$ .venv/bin/ruff check src/ scripts/ tests/ migrations/
All checks passed!

$ .venv/bin/alembic current
873b8b38eca0 (head)
```
