# CLAUDE.md

Контекст для Claude Code при работе с этим репозиторием.

## Что это за проект

Локальный трекер тендеров с goszakup.gov.kz. Скрейпит ежедневно по preset'ам
(один на каждый из 20 регионов РК), хранит в SQLite, отдаёт FastAPI UI с
фильтрами и отчётами. Запускается локально (cron/launchd на mac), не предполагает
multi-user.

См. README.md для обзора возможностей и команд.

## Окружение и базовые команды

- Python: `.venv/bin/python` (создаётся через `python3.13 -m venv .venv`). Не
  использовать системный `/usr/bin/python3` — там 3.9, который не понимает
  `float | None`.
- Установка: `.venv/bin/pip install -e .`
- Запуск CLI: `.venv/bin/python -m goszakup.cli ...`
- Запуск UI: `GZ_NO_AUTH=1 .venv/bin/python -m uvicorn goszakup.web.app:app --port 8765`
- БД: `data/goszakup.sqlite` (SQLite, без миграций — `Base.metadata.create_all`).
  Файл в WAL-режиме: рядом появляются `goszakup.sqlite-shm` и `goszakup.sqlite-wal`
  — это норма, в git не пушить (включено в `.gitignore`).
- ENV: подгружается из `./env` через `python-dotenv` (вызов в `config.py`,
  `override=False` — реальный shell-env приоритетнее). Обязательная переменная
  для LLM/чата — `CEREBRAS_API_KEY`. Опционально: `GZ_LLM_MODEL` (дефолт
  `gpt-oss-120b`), `GZ_NO_AUTH=1` (выключает Basic Auth для локалки),
  `GZ_USER`/`GZ_PASSWORD` (если auth включён).

## Архитектурные правила, которые легко нарушить

1. **Все HTTP-запросы к goszakup идут через `ThrottledSession`** из
   `scraper/http.py`. Она держит глобальный `Crawl-delay=5s` через `_lock`.
   Не делайте `requests.get(...)` напрямую и не запускайте две сессии
   параллельно — обе обходят rate-limit.

2. **`SearchParams.amount_from` по умолчанию = 500 000**. Это продуктовое
   решение, зафиксировано в `config.MIN_AMOUNT`. Не понижать без явной просьбы
   пользователя.

3. **Документы за кнопкой «Перейти» (`actionModalShowFiles`) скачиваются
   через ajax-эндпоинт анонимно.** Реальный путь:
   `GET /ru/announce/actionAjaxModalShowFiles/{anno_id}/{file_type_id}` —
   возвращает HTML-таблицу с прямыми ссылками на файлы на
   `v3bl.goszakup.gov.kz`. Авторизация НЕ нужна. Реализация —
   `scraper/modal_files.py`.
   ВАЖНО: качаем **только кандидатов в ТЗ** (предикат `is_tz_like_name`:
   «техническая спецификация», «конкурсная документация», `techspec_*`),
   а не все 12+ приложений объявления — иначе лишняя нагрузка на сайт и
   лишние LLM-токены. Ссылку из колонки «Подпись» (`/files/signature/`)
   игнорируем — это ЭЦП, не файл.

4. **`Lot.customer.bin` обычно пустой**. На страницах листинга и `tab=lots`
   goszakup не показывает БИН заказчика — только имя. БИН есть только у
   `Announcement.organizer` (берётся из `tab=general`). Поэтому в
   `_get_or_create_org` сначала ищем по БИН, потом по имени; это даёт
   возможность связать customer и organizer когда один и тот же.

5. **Закрытые лоты не пересканируются** (пользователь это явно подтвердил).
   Если статус ушёл в Состоялась/Не состоялась/Отказ — детальная карточка
   больше не дёргается, договор может остаться непроставленным. Если нужно
   подтянуть договоры — отдельный preset с `status_codes=PAST_STATUSES`.

6. **Тип `int | None` и подобные требуют Python 3.10+**. В проекте используется
   3.11+. Если видите `TypeError: unsupported operand type(s) for |` — значит
   запустили не тем интерпретатором.

7. **LLM-шаг (`classify/llm.py`) НЕ должен ронять `daily`**. Вызов к Cerebras
   обёрнут в `analyze_and_save()` с финальным `except Exception` — наружу
   ошибки не уходят. Случаи graceful-degradation (каждый — `log.warning`,
   без записи в БД): нет `CEREBRAS_API_KEY`, нет сети к Cerebras, битый/пустой
   PDF/DOCX, модель не вызвала tool → классификация по названию+ENSTRU с
   `analysis_confidence='low'` (если modal не вернул ТЗ).

8. **Идемпотентность LLM-анализа — по паре `(analyzer_version, tz_sha256)`**.
   Если оба совпадают с текущими — переанализ пропускается. `tz_sha256` берётся
   из уже посчитанного `Document.sha256` (не дублируем алгоритм). При правках
   промпта или схемы `AnalysisResult` менять `ANALYZER_VERSION` в `classify/llm.py`
   — следующий прогон автоматически перегонит старые записи. Текущая версия —
   `llm-v3-gpt-oss-120b-ru` (русский `tz_summary` обязателен в промпте, схеме
   Pydantic и tool-schema; не откатывайте без бампа версии).

9. **На goszakup LLM ходит ТОЛЬКО по явному действию пользователя.**
   Авто-LLM-классификация — это локальный шаг `daily`/`run-preset` после
   скачивания документов. В UI добавлены **три ручных действия** на странице
   `/lot/{id}` (см. `web/app.py`):
   - `POST /lot/{id}/analyze` → `analyze_and_save(force=True)` (бамп
     идемпотентности игнорируется);
   - `POST /lot/{id}/fetch_documents` → `fetch_announcement` + `_save_documents`
     по той же логике, что и в пайплайне (через `ThrottledSession`,
     ~30-90с на клик);
   - `POST /lot/{id}/chat` → `chat_about_lot`, история сообщений хранится в
     `localStorage` браузера, а не в БД.
   Все три — короткие POST'ы с 303-редиректом или JSON, не LLM-вызовы
   на каждый рендер. Поиск по `dev_category`/`vendor_lock_risk` — SQL по
   `lot_analyses`, без LLM.

10. **SQLite в WAL-режиме + `connect_args={"timeout": 30}`** (см. `db/engine.py`).
    Включается через event listener `_sqlite_pragmas` на каждом connect:
    `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL`. Без этого uvicorn
    (читатель) и `daily`/`reanalyze`/`fetch_documents` (писатели) валятся
    с `database is locked`, как только их транзакции пересекаются. WAL
    позволяет читать во время записи; параллельные писатели всё равно
    сериализуются, поэтому правило про «не запускать `daily` параллельно
    с `run-preset`» по-прежнему действует.

11. **`_call_llm` ретраит Cerebras 429 «queue_exceeded»** с бэкоффом 5/15/30с
    (3 ретрая, см. `_RETRY_DELAYS`). Это не «новая фича», а лечение
    burst-throttling: free-tier Cerebras душит при >30 req/мин. В массовых
    скриптах (`scripts/reanalyze_actual_it.py`) дополнительно ставится
    1.5с pacing между лотами — он держит темп под лимитом, чтобы ретрай
    вообще редко срабатывал.

## Где что лежит

- `scraper/search.py` — listing-парсер, основа табличной выдачи. Не путать с
  `scraper/announce.py`, который тянет детали.
- `scraper/announce.py` — 4 таба: general / lots / documents / contracts. Парсит
  таблицы по заголовкам колонок (idx mapping), а не по позиции.
- `scraper/statuses.py` — STATUS_NAMES (25 кодов, собрано из формы goszakup),
  ACTUAL_STATUSES, PAST_STATUSES.
- `scraper/katos.py` — 20 регионов. Коды для Абайской/Жетысуской/Улытауской
  (333/191/351) проверены на форме goszakup, но могут отличаться от
  официального справочника КАТО.
- `classify/it.py` — IT pre-filter: точное совпадение `enstru` (21 запись из
  фактической выборки) + regex-фоллбэк по ключевым словам.
- `classify/llm.py` — LLM-классификатор ТЗ + чат по ТЗ. Pydantic-схема +
  Cerebras tool calling (OpenAI-формат, `strict: True`); модель — `gpt-oss-120b`
  по умолчанию; `reasoning_effort="low"` (задача структурная); ANALYZER_VERSION
  для идемпотентности; `pick_tz_document` (предпочитает «техническую
  спецификацию» над «конкурсной документацией»; PDF > DOCX); `extract_text(path)`
  диспатчит PDF (pdfplumber) / DOCX (python-docx). Без OCR.
  - `analyze_and_save(session, lot, *, force=False)` — точка входа из пайплайна
    и UI-кнопки «Переанализировать». `force=True` обходит идемпотентность.
  - `chat_about_lot(lot, history)` — один поворот чата на странице лота.
    Системный промпт собирает поля лота + полный текст ТЗ из
    `_cached_tz_text` (`lru_cache(128)` поверх `extract_text` — чтобы при
    многократных вопросах не переоткрывать PDF). История — список
    `[{role, content}, ...]`; последнее сообщение должно быть от user.
  - `_call_llm` имеет ретрай 5/15/30с при `429`/`queue_exceeded`/`too_many_requests`.
- `web/app.py` — FastAPI. Помимо листингов и карточек содержит три POST'а
  на странице лота: `analyze`, `fetch_documents`, `chat`. Импортирует
  `_save_announcement`/`_save_documents` из `jobs.run_preset` (переиспользует
  ту же логику, что и пайплайн). На странице `/actual` и `/past` есть
  колонка «Короткое ТЗ» — берётся из `lot_analyses.tz_summary`,
  ограничена 3 строками через CSS `-webkit-line-clamp`.
- `scraper/modal_files.py` — разворачивает кнопку «Перейти» через ajax,
  возвращает прямые ссылки на файлы на `v3bl`. Содержит публичный
  `is_tz_like_name(text)` — переиспользуется в `classify/llm.pick_tz_document`.
- `jobs/run_preset.py` — пайплайн. Двухфазный: сначала listing+upsert stub'ов,
  потом details для новых/сменивших статус. IT-фильтр применяется на фазе 1.
  В фазе 2 `_save_documents` сначала качает прямые `<a href>`, затем для
  строк за «Перейти» проверяет `is_tz_like_name` и разворачивает modal.
  LLM-шаг — следом, только для лотов с `it_category`.
- `db/models.py` — 9 таблиц. `Organization` — общая для customer/organizer/supplier.
  `LotAnalysis` — 1:1 к `Lot` через FK + UniqueConstraint.
- `db/engine.py` — `init_db()` делает `create_all` + узкий `_ensure_columns()`
  для редких ALTER TABLE на существующих БД (сейчас только `scrape_runs.llm_analyzed`,
  `scrape_runs.note`). Engine создан с `connect_args={"timeout": 30}`; event
  listener `_sqlite_pragmas` на каждом connect выставляет `PRAGMA journal_mode=WAL`
  и `PRAGMA synchronous=NORMAL`. WAL включается на уровне файла БД и
  сохраняется между запусками.
- `config.py` — глобальные константы + `load_dotenv(ROOT / ".env", override=False)`
  при импорте модуля. Все точки входа (CLI, web, jobs) транзитивно зависят
  от `config`, поэтому `.env` гарантированно загружен до первого
  `os.environ.get(...)`.
- `scripts/reanalyze_actual_it.py` — одноразовый скрипт для массового
  переанализа после смены `ANALYZER_VERSION`. Идёт по всем актуальным
  IT-лотам, у которых нет анализа с текущей версией; 1.5с pacing между
  запросами, чтобы держать Cerebras под лимитом.

## Соглашения

- Тексты в шаблонах и комментариях — на русском.
- Не добавлять backwards-compat и feature-флаги без необходимости.
- Не писать docstring'и из одного утверждения «что делает функция». Имя
  функции и сигнатура говорят сами за себя. Комментарии — только про *почему*
  или нетривиальные ограничения (см. `documents.py` про шаблонные ссылки).
- Сообщения в БД (статусы, имена категорий) хранятся в исходном виде с
  goszakup, без переводов.

## Типичные операции

```bash
# Перегенерировать схему БД с нуля (стирает данные!)
rm -f data/goszakup.sqlite data/docs/* && \
    .venv/bin/python -m goszakup.cli init && \
    .venv/bin/python -m goszakup.cli seed-presets

# Прогнать только один регион
.venv/bin/python -m goszakup.cli run-preset 20  # Шымкент

# Узнать код статуса по имени (или наоборот) — посмотри scraper/statuses.py

# Узнать код КАТО — посмотри scraper/katos.py

# Логи launchd
ls -lt data/logs/ | head
```

## Что НЕ делать

- Не перенумеровывать preset'ы. ID используется в URL и в `scrape_runs.preset_id`.
- Не менять `DOCS_DIR` структуру (`data/docs/{announcement_id}/{filename}`) без
  миграции — пути на диске прописаны в `Document.local_path`.
- Не запускать `daily` параллельно с `run-preset` / `reanalyze` / массовым
  скриптом — обе HTTP-сессии независимо применяют `Crawl-delay`, итоговый rate
  превысит лимит. WAL разруливает чтения и записи, но два писателя по-прежнему
  сериализуются. Проверять `pgrep -f goszakup.cli` перед массовыми операциями.
- Не пушить в git: `data/goszakup.sqlite*` (включая `-wal`/`-shm`), `data/docs/`,
  `data/logs/`, `.env`, `.venv/`. Список собран в `.gitignore` (создан в этой
  репе, репо ещё не инициализирован).
- Не коммитить `CEREBRAS_API_KEY` в код или в фикстуры. Ключ — только в `.env`.

## Скрытые подводные камни

- Goszakup отдаёт `302 → /system_error/not_found` вместо `404` для несуществующих
  путей и для ajax-эндпоинтов без авторизации. Не путать с реальным редиректом.
- `count_record=50` в выдаче — фиксированный максимум, который сайт принимает.
  Увеличить нельзя.
- Sites returns total «N записей» только на странице ≥1, и иногда строка
  отсутствует — тогда полагаемся на «пустая страница → конец».
- Имена шаблонных документов на `v3bl.goszakup.gov.kz` идут с `//` после
  домена (`v3bl.goszakup.gov.kz//uploads/...`). Это не баг, не нормализовать —
  ссылка работает только так.
- **`analysis_confidence='low'` теперь редкость.** ТЗ массово скачиваются
  через modal_files; `low` остаётся только когда modal вернул пусто
  (необычные тендеры без техспеца), формат `.doc` (legacy, конвертер не
  тащим), битый PDF без текстового слоя.
- Cerebras (OpenAI-формат tool calling) возвращает `tool_calls[0].function.arguments`
  как **JSON-строку**, её надо явно `json.loads` (в отличие от Anthropic, где
  поле `.input` приходило уже dict-ом). Бамп `ANALYZER_VERSION` при смене
  провайдера/промпта обязателен — иначе старые записи с прежней семантикой
  останутся в БД из-за идемпотентности.
- В tool schema со `strict: True` обязательно `additionalProperties: false`
  на каждом object'е — Cerebras иначе отвергнет схему. Constrained decoding
  гарантирует валидный JSON и точное соответствие enum'ам.
- Эндпоинт `actionAjaxModalShowFiles` отвечает анонимным GET 200 (проверено).
  Цепочка фоллбэков в `modal_files._try_fetch` (XHR-заголовки → прогрев
  cookie) — заложена на случай, если goszakup усилит проверку; пишет в
  лог, какой шаг сработал. Если когда-нибудь увидите массовый
  `WARNING modal …: все попытки провалены` — значит сайт сменил
  механику, надо обновить цепочку.
- **Cerebras free-tier 429 burst** на ~30 req/мин. `_call_llm` ретраит до 3
  раз с бэкоффом 5/15/30с. В массовых прогонах добавляйте 1.5-2с pacing
  между лотами (см. `scripts/reanalyze_actual_it.py`). Если каждый лот всё
  равно фейлится — лимит превышен, паузу больше или подождать минуту.
- **`tz_summary` обязан быть на русском.** Правило вшито в трёх местах:
  блок «ВАЖНО» в `SYSTEM_PROMPT`, `description` в Pydantic `AnalysisResult`
  и `description` в `CLASSIFICATION_TOOL` (Cerebras `strict: True` ориентируется
  именно на tool-schema). При замечании «модель отвечает на английском» —
  усиливать промпт + бамп `ANALYZER_VERSION`, иначе старые записи останутся.
- **Кнопка «Загрузить документы» на /lot/{id}** работает и на stub-лотах (где
  `lot.announcement` ещё нет в БД, только `announcement_id`). `_save_announcement`
  внутри создаёт запись, если не нашёл. Не гейтить кнопку на `lot.announcement`
  — гейтить на `lot.announcement_id`.
