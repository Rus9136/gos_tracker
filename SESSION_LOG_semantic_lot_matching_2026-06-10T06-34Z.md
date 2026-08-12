# SESSION LOG — Семантический подбор лотов под NL-запрос пользователя

**Дата сессии:** 2026-06-10, ~05:00 — 06:35 UTC
**Цель:** дать каждому пользователю «память»-настройку — текстовый запрос
«какие лоты хочу видеть», и подбирать подходящие лоты через LLM поверх уже
накопленной базы лотов с документами.
**Статус:** MVP реализован и проверен (ruff/импорты/миграция/функц. тест ядра).
**Коммит:** не сделан — изменения в рабочем дереве (по запросу пользователя).

---

## TL;DR

Построен семантический слой подбора поверх детерминированного `User.scope`:

- **2 модели**: `UserQuery` (NL-предпочтение, версионируемое) + `UserLotMatch`
  (кеш матча `запрос × лот`, идемпотентный).
- **LLM-matcher** на Cerebras `gpt-oss-120b` со strict tool — матчит запрос
  против **`LotAnalysis.tz_summary`** (дешёвое summary), а не против PDF.
- **Fan-out** через Dramatiq: новый лот после анализа матчится против всех
  активных запросов (pre-filter по scope); правка запроса → backfill.
- **UI без LLM**: `/matched` читает кеш чистым SQL — ключевой принцип проекта
  «никаких LLM-вызовов на запросах UI» сохранён.

**Ключевая идея:** дорогое «понимание лота» (чтение ТЗ) уже сделано один раз
в `LotAnalysis`. Матч предпочтения — дёшево, против summary, с кешем на пару
`(query, lot)`, зеркалящим идемпотентность `LotAnalysis`.

**Проверки:** ruff (мои файлы — clean), импорты + регистрация actor'а, alembic
upgrade/downgrade на временной БД, функц. тест ядра со стаб-LLM.

---

## Архитектурное решение

❌ Не гонять LLM по всем лотам с их PDF на каждый запрос пользователя
(30K символов × N лотов × M пользователей — взрыв стоимости).

✅ Понимание лота посчитано один раз → `LotAnalysis.tz_summary` (2-3 предложения).
Матч предпочтения гоняем **против summary**, кешируем в `UserLotMatch`. На UI —
только SQL-чтение готовых матчей.

```
новый/изменённый лот → analyze_actor (LotAnalysis, как раньше)
        └─ enqueue_matches_for_lot()  ── pre-filter по scope ──> match_actor → UserLotMatch
правка/создание запроса → version++ → backfill_query() по актуальным лотам в scope
UI /matched → SELECT … WHERE matched ORDER BY score   (LLM не вызывается)
```

**Дефолты (одобрены пользователем):** несколько именованных запросов на
пользователя; score 0..100; forward-матчинг новых лотов + разовый backfill
актуальных в scope.

---

## Новые файлы

| Файл | Что |
|---|---|
| `src/goszakup/db/models.py` (+2 класса) | `UserQuery` (user_id, name, text, `compiled_filters` JSON, `version`, `active`) и `UserLotMatch` (user_query_id, lot_id, matched, score, reason, `matcher_version`, `query_version`; unique `(query, lot)`; cascade-delete). |
| `migrations/versions/a1b2c3d4e5f6_user_queries_and_lot_matches.py` | Создаёт обе таблицы + индексы. `down_revision='18c81fc2f4ab'` (был head). JSON→JSONB-вариант, TIMESTAMPTZ — как в models.py. |
| `src/goszakup/classify/matcher.py` | LLM-matcher. Cerebras `gpt-oss-120b`, strict tool `submit_match` (matched/score/reason), ретраи на 429 — паттерн `classify/llm.py`. `MATCHER_VERSION='match-v1-gpt-oss-120b'`. `match_and_save()` с идемпотентностью по `(query_version, matcher_version)`. Вход — `tz_summary` + поля лота, НЕ документ. |
| `src/goszakup/queue/matching.py` | `match_actor(query_id, lot_id)` (очередь `matching`). `enqueue_matches_for_lot(session, lot)` — fan-out по активным запросам с pre-filter `lot_in_scope`. `enqueue_matches_for_query(query, lots)` — backfill. |
| `src/goszakup/jobs/match.py` | `backfill_query(query_id, limit=2000, sync=False)` — матч запроса по актуальным лотам в scope. Async через очередь или `--sync` (без Redis, для тестов). Защитный потолок `DEFAULT_LIMIT` с логом при достижении. |
| `src/goszakup/scope.py` | `scope_conditions(user)` + `lot_in_scope(lot, user)` — вынесены из `web/app.py` как единый источник правды (переиспользует fan-out). |
| `src/goszakup/web/templates/queries.html` | UI: форма создания + таблица запросов (edit в `<details>`, toggle, delete), счётчик матчей. |
| `src/goszakup/web/templates/matched.html` | UI: лента подходящих лотов (балл, лот→`/lot/{id}`, заказчик, сумма, причина), фильтр-пилюли по запросам. |

## Изменённые файлы

| Файл | Что |
|---|---|
| `src/goszakup/queue/actors.py` | В `analyze_actor` после commit нового анализа — `enqueue_matches_for_lot(session, lot)`. Импорт `from .matching import …` регистрирует `match_actor` (воркер грузит `goszakup.queue.actors`). |
| `src/goszakup/web/app.py` | Роуты `/queries` (GET/POST create/edit/toggle/delete, per-user через `require_user`) и `/matched` (GET, SQL-вью). `_scope_conditions`/`_lot_in_scope` → алиасы на `scope.py`. `_nav_active`: ветки `matched`/`queries`. `_trigger_backfill()` обёрнут в try/except (UI не падает при недоступном брокере). |
| `src/goszakup/web/templates/_layout.html` | Группа навигации «Подбор» → «Подходящие» (`/matched`) и «Мои запросы» (`/queries`), видны всем пользователям. |
| `src/goszakup/cli.py` | Команды `queries` (список) и `match-backfill <id> [--limit] [--sync]`. |

---

## Идемпотентность и версионирование

Зеркалит `LotAnalysis`:
- `UserLotMatch` уникален по `(user_query_id, lot_id)`.
- Пересчёт пропускается, если у пары совпадают `query_version` И `matcher_version`.
- Правка текста запроса → `UserQuery.version += 1` → старые матчи инвалидируются
  и пересчитываются (через upsert, без дублей строк).
- На ошибке LLM запись НЕ создаётся — чтобы при следующем прогоне с рабочим
  ключом пара переанализировалась (та же логика, что в `classify/llm.py`).

---

## Проверки

| Проверка | Результат |
|---|---|
| `ruff check` по моим файлам | **All checks passed** |
| `ruff check src/` остаток | 2 замечания в `llm.py` / `announce.py` — **пред­существующие**, файлы не трогал |
| Импорт `queue.actors`/`web.app`/`jobs.match` | OK |
| Регистрация actor'а | `match_actor` в брокере, очередь `matching` |
| `alembic upgrade head` на чистой SQLite | OK, единый head `a1b2c3d4e5f6` |
| `alembic downgrade -1` → `upgrade head` | обратимо |
| Колонки `user_lot_matches` | соответствуют модели |
| Функц. тест ядра (стаб-LLM) | первый матч сохранён → повтор идемпотентен (без LLM) → `version++` пересчитал через upsert (1 строка) |

---

## Эксплуатация

- Fan-out и backfill ставят задачи в **Dramatiq → нужен воркер + Redis**. Без
  них forward-матчинг откладывается; UI при недоступном брокере не падает.
- Backfill догоняется командой: `python -m goszakup match-backfill <id>`
  (или `--sync` для прогона без очереди).
- Матчер требует `CEREBRAS_API_KEY` (как и существующий анализатор).
- Воркер очереди `matching` можно поднять отдельно от listing/detail/llm.

---

## Не вошло в MVP (расширения по плану)

- **Компилятор запроса** → `compiled_filters` (`{dev_category, max_amount, kato}`):
  поле в модели есть, наполнение пока NULL. Даст до-LLM отсев SQL'ом.
- **Reuse по simhash**: для шаблонных тендеров (`LotAnalysis.reused_from_lot_id`)
  копировать матч без LLM.
- **Embeddings-ранжирование** при росте до тысяч матчей.

---

## Связанное

- Паттерн LLM-вызова: `src/goszakup/classify/llm.py` (`_call_llm`, strict tool,
  ретраи 429) — matcher повторяет его один-в-один.
- Источник «понимания лота»: `LotAnalysis.tz_summary`.
- Сопутствующий документ (другая задача этой сессии): `TENDER_ECP_SIGNING_GUIDE.md`
  — программная подпись тендерных заявок через ЭЦП (НУЦ РК).

---

## Аддендум: доводка (сессия 2026-06-10, позже)

Ревизия MVP нашла и закрыла пробелы:

1. **Stale-матчи при переанализе лота** (главное): идемпотентность была только
   по `(query_version, matcher_version)` — переанализ лота (бамп
   `ANALYZER_VERSION`, кнопка «Переанализировать») менял `tz_summary`, а матчи
   оставались навсегда. Теперь `match_and_save` пересчитывает пару, если
   `matched_at` старше `LotAnalysis.analyzed_at`.
2. **Fan-out был только в `analyze_actor`**: добавлен в sync-путь
   `run_preset` (после commit) и в POST `/lot/{id}/analyze` — оба с
   try/except (без брокера не валятся).
3. **GZ_NO_AUTH dev-админ (id=0, нет в `users`)**: inner join в fan-out молча
   выкидывал его запросы, backfill отказывался («user не найден»). Теперь
   outerjoin + отсутствующий владелец = scope без ограничений.
4. **Удаление пользователя с запросами падало бы на PG по FK** — `/users/{id}/delete`
   теперь сначала удаляет его UserQuery (матчи каскадом).
5. **Очередь переименована `matching` → `goszakup_matching`** (конвенция
   префикса — dev-Redis может быть общим).
6. **Промпт матчера**: вместо голого КАТО-кода — имя региона (`region_name`).
7. **Тесты** (+20): `test_scope.py`, `test_matcher_match.py` (идемпотентность,
   upsert, staleness анализа, ошибки LLM), `test_matching_fanout.py`,
   `test_queries_routes.py` (CRUD + /matched). conftest чистит новые таблицы.
8. CLAUDE.md: правило #17 + карта файлов.

Полный прогон: 108 passed; ruff по затронутым файлам clean; alembic
upgrade/downgrade обратимы, head единый `a1b2c3d4e5f6`.
