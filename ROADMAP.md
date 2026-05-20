# Roadmap

Долгосрочный план работ. Обновляется по мере прогресса: помечайте `[x]`
выполненные пункты, переносите между секциями («В работе» → «Готово»),
дописывайте новые идеи в backlog.

Условные обозначения:
- `[x]` — сделано
- `[ ]` — запланировано / в работе
- `(p1)`, `(p2)`, `(p3)` — приоритет (p1 — ближайшие, p3 — «когда дойдут руки»)

Последнее обновление: 2026-05-19 (вечер).

---

## Текущий статус

| Метрика | Значение |
|---|---|
| Версия | 0.1.0 (MVP) |
| БД-таблиц | 9 (`lot_analyses` + WAL-режим SQLite) |
| Скрейпер-табов разобрано | 4 из 7 (general/lots/documents/contracts) |
| Веб-страниц | 8 (+3 POST'а на странице лота: analyze, fetch_documents, chat) |
| Default-preset'ов | 20 (по регионам РК) |
| Шедулер | launchd plist готов, активирован (видимо daily в 6:00) |
| Авторизация UI | HTTP Basic (одна учётка) |
| Доступ к ТЗ за «Перейти» | анонимно через `actionAjaxModalShowFiles` |
| Классификация | enstru + keyword + LLM (Cerebras `gpt-oss-120b-ru`, чтение PDF/DOCX) |
| LLM-чат на /lot/{id} | есть (история в localStorage, контекст = ТЗ + поля лота) |
| Ручные действия на /lot/{id} | анализ / скачать документы / чат |
| `tz_summary` язык | только русский (вшито в промпт + Pydantic + tool-schema) |
| Колонка «Короткое ТЗ» в /actual и /past | есть |
| Уведомления | нет |

---

## Готово

### M1 — ядро (парсер + БД + классификатор)
- [x] Скелет проекта (pyproject.toml, src layout, venv с Python 3.13)
- [x] SQLAlchemy 2.0 модели: organizations, announcements, lots,
      lot_status_history, documents, contracts, presets, scrape_runs
- [x] Справочник КАТО 20 регионов РК (`scraper/katos.py`)
- [x] Справочник 25 кодов статусов + группы ACTUAL/PAST (`scraper/statuses.py`)
- [x] `ThrottledSession` — глобальный rate-limit 5 c между запросами
- [x] Универсальный listing-скрейпер `iter_listing(SearchParams)`
- [x] Детальный парсер карточки объявления (4 таба):
      general (организатор, контакт, способ), lots (price/qty/unit/plan), 
      documents (имя/признак/file_type_id/url), contracts (поставщик, БИН, факт)
- [x] Загрузчик документов с sha256, дедупом, безопасным переименованием
- [x] IT-классификатор: 27 enstru-маппингов + regex-фоллбэк по ключам
- [x] Пайплайн `run_preset`: listing → diff → details для новых/изменённых
      → upsert organizations/announcements/lots → запись lot_status_history
      → скачивание новых документов → журналирование в scrape_runs
- [x] Smoke-тест прошёл end-to-end на Шымкент / Услуги ИТ / Опубликован

### M2 — оркестрация (ежедневный запуск)
- [x] Seed 20 default-preset'ов (по одному на регион, статусы ACTUAL, min 500K)
- [x] `jobs/daily.py` — обход всех активных preset'ов
- [x] CLI с typer: init, seed-presets, presets, run-preset, run-once, daily, stats
- [x] `scripts/run_daily.sh` + `scripts/com.user.goszakup.daily.plist`
- [x] `scripts/install_launchd.sh` — установка агента в `~/Library/LaunchAgents`
- [x] Логирование прогонов в `data/logs/daily-YYYYmmdd-HHMMSS.log`

### M3 — веб-интерфейс
- [x] FastAPI + Jinja2 + Tailwind (CDN) + HTMX (CDN)
- [x] HTTP Basic auth с одной учёткой из ENV (или `GZ_NO_AUTH=1` для локалки)
- [x] Страницы: `/`, `/actual`, `/past`, `/lot/{id}`, `/organizations`,
      `/organization/{id}`, `/presets` (+toggle), `/runs`,
      `/document/{id}/download`
- [x] Фильтры на актуальных/прошедших: поиск, регион, IT-категория,
      диапазон суммы, сортировка
- [x] Пагинация по 50 строк
- [x] Дашборд: счётчики, разбивка по регионам/категориям, последние 15 прогонов
- [x] Хронология статуса в карточке лота, ссылки на документы
- [x] Карточка организации с разделением actual / past

### M4 — документация
- [x] README.md (для пользователя)
- [x] CLAUDE.md (для будущих сессий Claude Code)
- [x] ROADMAP.md (этот файл)

### M8 — Ручные LLM-инструменты в UI + русский язык + concurrency (2026-05-19, вечер)
- [x] Кнопка «Запустить LLM-анализ» / «Переанализировать» на `/lot/{id}`
      (POST `/lot/{id}/analyze` → `analyze_and_save(force=True)`). Видна,
      когда есть хотя бы один скачанный документ и `lot.it_category`.
- [x] Кнопка «Загрузить документы» / «Обновить документы» на `/lot/{id}`
      (POST `/lot/{id}/fetch_documents` → `fetch_announcement` +
      `_save_announcement` + `_save_documents`). Работает и на stub-лотах
      без записи Announcement; ThrottledSession создаётся per-request.
      303-редирект с `?docs=<кол-во новых>` / `?fetch_error=...`.
- [x] Чат «по ТЗ» на `/lot/{id}` (POST `/lot/{id}/chat`). История
      хранится в `localStorage[lot_chat_<id>]` (без БД), системный промпт
      каждый раз собирает поля лота + полный текст ТЗ через
      `_cached_tz_text` (`lru_cache(128)` над `extract_text`).
      Cmd/Ctrl+Enter — отправить, «Очистить» — стереть.
- [x] `tz_summary` строго на русском: блок «ВАЖНО» в `SYSTEM_PROMPT`,
      `description` в Pydantic-схеме и `CLASSIFICATION_TOOL`. Бамп
      `ANALYZER_VERSION` → `llm-v3-gpt-oss-120b-ru`.
- [x] Колонка «Короткое ТЗ» на `/actual` и `/past` (берётся из
      `lot_analyses.tz_summary`, 3 строки через CSS `-webkit-line-clamp`,
      полный текст в `title=`).
- [x] `python-dotenv` подключён в `config.py` (`load_dotenv(ROOT/.env, override=False)`).
      `.env` и `.gitignore` созданы.
- [x] SQLite в WAL-режиме + `connect_args={"timeout": 30}` (`db/engine.py`,
      event listener `_sqlite_pragmas`). Без этого uvicorn + параллельный
      `daily`/`reanalyze` валились с `database is locked`.
- [x] Ретрай 429 в `_call_llm` (5/15/30с бэкофф) — Cerebras free-tier
      душит при >30 req/мин. + 1.5с pacing в `scripts/reanalyze_actual_it.py`.
- [x] Массовый бэкфилл: `scripts/reanalyze_actual_it.py` перегнал ~180
      актуальных IT-лотов на новую `ANALYZER_VERSION` с русским `tz_summary`.
      Часть осталась незавершённой из-за параллельно работавшего launchd-`daily`.

### M7 — Переход на Cerebras Inference (2026-05-19)
- [x] LLM-провайдер сменён: Anthropic Claude Haiku → Cerebras `gpt-oss-120b`.
      Причина — open-weights модель с быстрым инференсом, strict-режим
      tool calling (constrained decoding гарантирует валидный JSON).
- [x] `classify/llm.py` переписан под OpenAI-формат tools
      (`type: function`, `strict: true`, `additionalProperties: false`),
      парсинг `tool_calls[0].function.arguments` через `json.loads`.
      `reasoning_effort="low"` для классификации.
- [x] ENV переименован: `ANTHROPIC_API_KEY` → `CEREBRAS_API_KEY`,
      дефолт `GZ_LLM_MODEL` = `gpt-oss-120b`.
- [x] `ANALYZER_VERSION` bumped → `llm-v2-gpt-oss-120b`: при следующем
      `daily` или `reanalyze` все прежние записи будут переанализированы.
- [x] Зависимости: `anthropic` удалён, `cerebras-cloud-sdk` добавлен.
- [x] Доки (README, CLAUDE) обновлены.

### M6 — Реальный доступ к ТЗ (2026-05-19)
- [x] Развёрнут реальный механизм кнопки «Перейти»: `GET
      /ru/announce/actionAjaxModalShowFiles/{anno}/{file_type}` —
      анонимно, GET, отвечает HTML-таблицей. Заметка про «требует
      авторизации» в CLAUDE.md/README была неверной — исправлено.
- [x] `scraper/modal_files.py` с fallback-цепочкой (plain → XHR →
      warmup+XHR) и логом, какой шаг сработал; парсинг по idx-mapping
      заголовков; игнор ссылок ЭЦП.
- [x] Приоритизация: качаем только кандидатов в ТЗ (предикат
      `is_tz_like_name` — техническая спецификация / конкурсная
      документация / `techspec_*`), а не все 12+ приложений.
- [x] DOCX в дополнение к PDF: `python-docx` в deps, диспатчер
      `extract_text(path)`, `.doc` пропускается с логом (не падает).
- [x] `pick_tz_document` обновлён: PDF > DOCX, узкая «техническая
      спецификация» предпочтительнее «конкурсной документации»
      (последняя — шаблон с обёрткой, в ней ТЗ внутри).
- [x] `scraper/auth.py` удалён — больше не нужен.
- [x] Документация: CLAUDE.md правило 3 переписано на реальную
      механику, README known issues 2/5 обновлены, в шапке ROADMAP
      убран блокер «авторизация», p2 «авторизация на goszakup» закрыт.
- [x] Smoke на 16970076: 2 ТЗ-кандидата скачаны (techspec_*.pdf,
      konkurs_doc_*.pdf), `pick_tz_document` выбрал техспец,
      `extract_text` вернул ~19K символов.

### M5 — LLM-классификация ТЗ (2026-05-19)
- [x] Таблица `lot_analyses` 1:1 к `lots` (dev_category, tech_stack,
      tz_summary, solo_feasible, vendor_lock_risk, analysis_confidence,
      analyzer_version, tz_sha256, source_document_id)
- [x] `_ensure_columns()` в `db/engine.py` — узкие ALTER TABLE для
      существующих БД (сейчас только `scrape_runs.llm_analyzed`)
- [x] `classify/llm.py`: pdfplumber для PDF без OCR, Pydantic
      `AnalysisResult`, Anthropic tool use со строгой схемой, промпт с
      разграничением 8 категорий и vendor-lock эвристик, идемпотентность
      `(ANALYZER_VERSION, tz_sha256)`
- [x] `scraper/auth.py` — заглушка `get_authenticated_session() → None`
      с готовым интерфейсом под p2
- [x] Встраивание в `jobs/run_preset.py` сразу после `_save_documents`,
      graceful degradation (нет ключа / нет сети / битый PDF / ТЗ за
      авторизацией — лог, прогон не падает)
- [x] CLI `reanalyze` (`--lot-id`, `--limit`, `--force`) для бэкфилла
      без обращения к goszakup
- [x] UI: фильтры `dev` и `risk` на `/actual` и `/past` (по аналогии с
      `it`), бейджи в таблице, секция «LLM-анализ ТЗ» в `/lot/{id}`,
      колонка `llm` в журнале прогонов
- [x] Дефолт модели — `claude-haiku-4-5-20251001`, перебивается
      `GZ_LLM_MODEL`. Ключ — `ANTHROPIC_API_KEY`.

---

## В работе

_Сейчас пусто. Добавьте сюда то, чем занимаетесь, чтобы видеть фокус._

---

## Следующее — приоритет p1 (ближайшие 1-2 недели)

- [ ] **Первый полный прогон по всем 20 регионам.** Запустить `daily`
      вручную, измерить реальное время, оценить размер БД и количество
      скачанных файлов. На основе данных можно править preset'ы или
      добавить параллелизм (с осторожностью к rate-limit).
- [ ] **Активация launchd**: запустить `scripts/install_launchd.sh`, проверить
      что в назначенное время агент стартует, логи пишутся.
- [ ] **Доработать LLM-промпт по результатам первого полного прогона.**
      Выбрать из `lot_analyses` лоты, где категория явно неверна (особенно
      граница `1c_development` ↔ `software_support` и
      `web_development` ↔ `not_dev` для проектно-сметной), скорректировать
      разграничения в SYSTEM_PROMPT, поднять `ANALYZER_VERSION` →
      следующий `daily` или `reanalyze --force` перегонит.
- [ ] **Параллельно — расширить enstru pre-filter** (`classify/it.py`):
      открыть БД, выбрать enstru, у которых `it_category IS NULL`, но
      `lot_analyses.dev_category != 'not_dev'` — добавить в
      `ENSTRU_TO_CATEGORY` или `KEYWORD_RULES`. Так LLM-вызовы не тратятся
      на лоты, которые pre-filter мог отсечь сам.
- [ ] **Дедуп организаций по нормализованному названию.** Сейчас
      «КГУ "Управление образования…"» и «Коммунальное государственное
      учреждение "Управление образования…"» — две разные записи. Добавить
      `Organization.normalized_name` и upsert по нему.
- [ ] **Тесты на парсеры** (хотя бы один happy-path на каждый из 4 табов
      и один на listing) — pytest + сохранённые HTML-фикстуры в `tests/fixtures/`.

## Следующее — приоритет p2 (1 месяц)

- [ ] **CRUD preset'ов через UI**: форма создания/редактирования с выбором
      KATO, диапазона суммы, мульти-выбора статусов и IT-категорий. Сейчас
      есть только toggle и seed через CLI.
- [ ] **Глобальный поиск** по карточкам лотов и заказчиков (одна строка
      сверху в layout).
- [ ] **Экспорт CSV** с текущей страницы actual/past (с теми же фильтрами).
- [ ] **Скачать все документы лота zip'ом** одной кнопкой.
- [ ] **Пересканирование закрытых лотов** для подтягивания договоров.
      Отдельный preset со `status_codes=PAST_STATUSES`, запускается раз в
      неделю (а не ежедневно).
- [ ] **/lot/{id}: визуализация хронологии статусов** временной шкалой,
      а не таблицей.
- [ ] **Отчёт по поставщикам** аналогично `/organizations`, но через
      contracts.supplier_id (кто выигрывает тендеры, на какие суммы).
- [ ] **Метрика «время в статусе»**: сколько лот пролежал в каждом
      статусе (берётся из `lot_status_history`).

## Следующее — приоритет p3 (когда дойдут руки)

- [ ] **Telegram-бот**: при появлении нового лота под подписанный preset
      отправлять уведомление. Подписки хранить в БД, бот в отдельном
      процессе. (Пока пользователь сказал «не нужно».)
- [ ] **Email-дайджест раз в неделю** с новыми лотами и сменами статусов.
- [ ] **Парсинг таба protocols** (PDF-протоколов) и таба appeal (апелляции).
- [ ] **Аналитика на дашборде**: график «новые лоты в день», диаграмма
      распределения по способам закупки, средняя сумма по регионам.
- [ ] **Сравнение лотов**: побочные лоты от одного заказчика, повторяющиеся
      закупки одного и того же.
- [ ] **API-эндпоинты** (JSON) для интеграций — те же фильтры что в UI.
- [ ] **Тёмная / светлая темы** с переключателем (сейчас только тёмная).
- [ ] **Локализация** на казахский (если понадобится).
- [ ] **Импорт стандартного справочника КАТО** для уточнения районов внутри
      областей. Пока работаем только на уровне области.
- [ ] **Миграция на PostgreSQL** если БД переваливает за ~5 ГБ или появится
      потребность в multi-user.

---

## Известные проблемы (с планом решения)

| Что | Влияние | План |
|---|---|---|
| BIN заказчика не показан в листинге и `tab=lots` goszakup | На странице `/organizations` группировка по имени — возможны дубли орфографии | Дедуп через нормализованное имя (p1) + по возможности подтягивать BIN из профиля заказчика отдельным запросом |
| Старый `.doc` (без `x`) не парсится | редкие лоты получают `confidence='low'` даже когда ТЗ скачан | Если станет массовым — добавить antiword или LibreOffice headless (p3) |
| Лимит выдачи листинга 10 000 строк | Большие preset'ы могут не дотягивать «хвост» | Если попадаем в лимит — сузить диапазон суммы или разбить preset на под-диапазоны |
| Закрытые лоты не пересканируются | Не появляются договоры на лотах, ушедших в PAST до того, как мы их увидели | Отдельный preset для PAST (p2), запускать реже |
| Коды КАТО Абайской/Жетысуской/Улытауской подобраны эмпирически (333/191/351) | На границе с соседними регионами могут быть пересечения | Сверить с официальным справочником КАТО (p3) |
| Нет тестов | Регрессия при обновлении сайта незаметна, пока не сломается прогон | Pytest + HTML-фикстуры (p1) |
| `GZ_USER`/`GZ_PASSWORD` лежат в plain ENV, дефолт `admin/admin` | Если кто-то откроет порт наружу — компрометация | Перейти на bcrypt-хеш в `.env`, проверка через passlib (p2) |
| Cerebras free-tier 429 «queue_exceeded» при бурстах | Массовые скрипты теряли лоты, теперь — ждут | Ретрай 5/15/30с в `_call_llm` + 1.5с pacing в скриптах. При систематических 429 на каждом лоте — поднять paid tier или увеличить pacing |
| Два параллельных писателя SQLite (uvicorn-write + daily/reanalyze) сериализуются | Долгий писатель блокирует короткий | WAL + `timeout=30` лечат «database is locked». Перед массовыми операциями всё равно проверять `pgrep -f goszakup.cli` — параллельный `daily` будет тормозить |
| Stub-лоты без записи Announcement | Кнопка «Загрузить документы» работает, но прежняя версия шаблона гейтила её на `lot.announcement` | Гейт исправлен на `lot.announcement_id`; `_save_announcement` сам создаёт запись |

---

## Соглашения по этому документу

- Когда что-то закончено — переносите из «Следующее» в «Готово» с пометкой
  даты в скобках.
- Когда находите проблему — пишите её в «Известные проблемы» с конкретным
  планом, а не «надо подумать».
- Если идея сырая — пишите в p3 (backlog). Не наполняйте p1/p2 «вкусным,
  но не срочным».
- Раз в неделю просматривайте p1: что переехало в p2/p3, что
  стало неактуальным.
