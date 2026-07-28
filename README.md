# Goszakup Tracker

Трекер IT-тендеров с [goszakup.gov.kz](https://goszakup.gov.kz): ежедневный
инжест через официальный API OWS (с HTML-фолбэком), история статусов,
LLM-анализ техзаданий, семантический подбор лотов под запросы пользователей
с Telegram-уведомлениями, multi-user веб-интерфейс и отчёты по заказчикам.
Развёрнут на <https://gost.salemsoft.kz> (production).

Карта проекта — фичи и их связи, конвейер задач, модель данных, матрица
возможностей OWS API — в [ARCHITECTURE.md](ARCHITECTURE.md).

## Возможности

- Источник данных — официальный API OWS (GraphQL + REST, Bearer-токен) с
  автоматическим фолбэком на HTML-скрейпинг; с токеном daily идёт одним
  инкрементальным проходом по `lastUpdateDate` вместо 20 региональных обходов.
- 20 preset'ов из коробки — по одному на каждый регион Казахстана. Минимальная
  сумма лота 500 000 ₸ (зашита в `config.py`).
- HTML-парсер уважает `Crawl-delay: 5s` из robots.txt goszakup; у API свой
  независимый rate-limit.
- Парсинг 4 табов карточки объявления: общие сведения, лоты, документы, договоры.
- Загрузка документов: прямые `<a href>` + разворот кнопки «Перейти» через
  ajax-эндпоинт (`actionAjaxModalShowFiles`). Качаются только кандидаты
  в ТЗ — техническая спецификация и конкурсная документация. Остальные
  приложения (договор, квал. формы) намеренно пропускаются.
- История смены статуса лота (`lot_status_history`).
- Трёхступенчатая классификация:
  - **enstru exact** (быстрый pre-filter): Оборудование / Услуги ИТ / ПО / Связь.
  - **keyword regex** для незнакомых enstru.
  - **LLM** (опц., при `CEREBRAS_API_KEY`) читает PDF/DOCX тех.задания и
    заполняет `dev_category` (1С-разработка / веб / мобильное / интеграция /
    поддержка / инфра / железо / не-разработка), стек, краткое summary,
    признак «справится один разработчик», `vendor_lock_risk`. Дефолтная
    модель — `gpt-oss-120b` (Cerebras Inference).
- Семантический подбор: пользователь описывает словами «какие лоты хочу»,
  LLM матчит запрос против summary ТЗ, новые матчи прилетают в Telegram
  (с кнопкой «Подробнее» — объяснение лота простым языком).
- Ретроспектива цен: заявки участников с ценами, скидками и статусами
  (Победитель/Отклонено/…) подтягиваются из API после дедлайна.
- Multi-user веб-интерфейс: дашборд, актуальные / прошедшие тендеры с
  фильтрами (включая тип разработки и риск заточки), карточка лота со
  скачиванием документов, LLM-анализом и чатом по ТЗ, отчёты по
  заказчикам/организаторам, журнал прогонов. У каждого пользователя свой
  scope на чтение (регионы / категории / мин. сумма).
- Ежедневный запуск по расписанию в 06:00 (systemd-timer) + Dramatiq-воркер
  с Redis-очередями ([CLAUDE.md](CLAUDE.md) → раздел «Продакшн»).

## Требования

- Linux (production) или macOS (single-user)
- Python 3.11+ (на проде используется 3.12)
- ~300 МБ места под БД и документы (растёт с количеством отслеживаемых лотов)

## Установка

### Вариант A — Docker (рекомендован для dev)

```bash
cp env.example .env  # как минимум CEREBRAS_API_KEY
docker compose up -d
docker compose exec web alembic upgrade head
docker compose exec web python -m goszakup.cli seed-presets
# UI: http://localhost:8766 (порт 8766, потому что 8765 обычно занят
# боевым systemd-сервисом)
```

Поднимает Postgres 16 + uvicorn в контейнере. Прогнать daily вручную:
`docker compose run --rm web python -m goszakup.cli daily`.

### Вариант B — venv (без Docker; без `GZ_DATABASE_URL` — SQLite)

```bash
cd <путь-к-репозиторию>
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/alembic upgrade head
.venv/bin/python -m goszakup.cli seed-presets
```

Боевой деплой под Linux+nginx+systemd описан в [DEPLOY.md](DEPLOY.md);
фактическая раскладка продакшна (Postgres 15, Dramatiq-воркер с выделенным
Redis, systemd-таймеры) — в [CLAUDE.md](CLAUDE.md), раздел
«Продакшн: gost.salemsoft.kz».

### Миграция данных из SQLite в Postgres

После того как Postgres-сторона поднята и накатана `alembic upgrade head`:

```bash
GZ_DEST_DATABASE_URL='postgresql+psycopg://goszakup:goszakup_dev@localhost:5433/goszakup' \
    .venv/bin/python -m scripts.migrate_sqlite_to_pg
```

Скрипт идёт по таблицам в порядке FK, идемпотентен (ON CONFLICT DO NOTHING),
создаёт stub-Announcement для orphan-ссылок (типичный кейс stub-лотов).

## Использование

### CLI

```bash
.venv/bin/python -m goszakup.cli --help

# Сводка по БД
.venv/bin/python -m goszakup.cli stats

# Список preset'ов
.venv/bin/python -m goszakup.cli presets

# Прогнать один preset (по id из `presets`)
.venv/bin/python -m goszakup.cli run-preset 20      # Шымкент

# Прогнать все активные preset'ы (то, что вызывается ежедневным таймером)
.venv/bin/python -m goszakup.cli daily

# Разовый прогон без сохранения preset'а
.venv/bin/python -m goszakup.cli run-once \
    --kato 790000000 \
    --status 210 \
    --it "Услуги ИТ" \
    --amount-from 5000000 --amount-to 100000000

# Прогнать LLM-классификацию по уже скачанным лотам
# (бэкфилл после внедрения или после правки промпта)
.venv/bin/python -m goszakup.cli reanalyze              # до 50 непроанализированных
.venv/bin/python -m goszakup.cli reanalyze --lot-id 42  # один лот
.venv/bin/python -m goszakup.cli reanalyze --force      # перегнать всё (новая версия промпта)
```

### Веб-интерфейс

```bash
# Без авторизации (только для дев-машины — на проде НЕ выставлять)
GZ_NO_AUTH=1 .venv/bin/python -m uvicorn goszakup.web.app:app --port 8765
```

Открыть [http://127.0.0.1:8765](http://127.0.0.1:8765).

Аутентификация — форма `/login` и таблица `users` (bcrypt + cookie-сессия,
на проде обязателен `GZ_SECRET_KEY`). `GZ_USER`/`GZ_PASSWORD` — только сид
первого админа при пустой таблице; дальше пользователи заводятся через UI
`/users` или `cli create-user --admin`.

Маршруты:

| URL | Что показывает |
|---|---|
| `/` | Дашборд: счётчики, сводка по регионам и категориям, последние прогоны |
| `/actual` | Актуальные тендеры с фильтрами (регион, IT-категория, **тип разработки**, **риск заточки**, диапазон суммы) |
| `/past` | Прошедшие тендеры (Состоялась/Не состоялась/Отменён/Отказ) с теми же фильтрами |
| `/starred` | Избранные лоты |
| `/matched` | Лоты, подобранные под семантические запросы пользователя |
| `/queries` | Семантические запросы: создание, правка, ручной пересчёт |
| `/lot/{id}` | Карточка лота: поля, **LLM-анализ ТЗ**, чат по ТЗ, история статусов, документы, договоры, заявки конкурентов с ценами |
| `/organizations` | Заказчики/организаторы с агрегатами (кол-во лотов, сумма) |
| `/organization/{id}` | Все лоты конкретной организации (+ отчёт по закупкам, admin) |
| `/settings` | Telegram-уведомления своего профиля |
| `/presets` | Список preset'ов, кнопка toggle active (admin) |
| `/scan`, `/ingest` | Ad-hoc прогоны: по фильтрам / по БИН заказчика (admin) |
| `/runs` | Журнал прогонов парсера (admin) |
| `/users` | Управление пользователями и их scope (admin) |
| `/expenses` | Расход LLM-токенов по дням/неделям/месяцам (admin) |
| `/submissions` | Статусы автоподач заявок (admin) |
| `/document/{id}/download` | Отдаёт локально сохранённый файл |

Полная таблица (42 роута с ролями) — [ARCHITECTURE.md](ARCHITECTURE.md), § 4.

### Ежедневный запуск по расписанию

- **Linux (production)** — systemd-timer `goszakup-daily.timer`, см.
  [DEPLOY.md](DEPLOY.md) § 8 и [CLAUDE.md](CLAUDE.md) → «Продакшн». Логи —
  через `journalctl -u goszakup-daily`.
- **macOS (опционально)** — launchd-агент в `scripts/`:

  ```bash
  bash scripts/install_launchd.sh
  launchctl list | grep goszakup
  launchctl start com.user.goszakup.daily          # вручную вне расписания
  launchctl unload ~/Library/LaunchAgents/com.user.goszakup.daily.plist
  ```

  Логи macOS-варианта: `data/logs/daily-YYYYmmdd-HHMMSS.log`.

## Структура

```
goszakup/
├── src/goszakup/
│   ├── config.py            # пути, MIN_AMOUNT, env-переменные
│   ├── cli.py               # точка входа Typer
│   ├── sources.py           # DataSource: ApiSource (OWS) + HtmlSource + фолбэк
│   ├── scope.py             # multi-user scope на чтение
│   ├── api/                 # клиент официального API OWS (GraphQL + REST)
│   ├── scraper/             # HTML-путь: ThrottledSession, листинг, карточка, файлы
│   ├── classify/            # IT pre-filter, LLM-анализ ТЗ, матчер запросов, учёт токенов
│   ├── jobs/                # пайплайн: run_preset, daily, incremental, contracts, bids, expire, health, ...
│   ├── queue/               # Dramatiq-акторы и очереди (broker, actors, matching, notify, autosubmit)
│   ├── notify/              # Telegram-уведомления
│   ├── vault/               # KeyVault автоподачи (AES-256-GCM)
│   ├── autosubmit/          # ядро автоподачи: тайминг, диспетчер, RPC к агенту
│   ├── db/                  # SQLAlchemy-модели (16 таблиц) + engine
│   └── web/                 # FastAPI: роуты, auth, Jinja2-шаблоны
├── agent/                   # submit-agent автоподачи (отдельный деплой на macOS-узле)
├── migrations/              # Alembic
├── docs/ows/                # снимок документации OWS + интроспекция GraphQL-схемы
├── scripts/                 # systemd-юниты, бэкофиллы, миграция SQLite→PG
├── data/                    # БД (dev-SQLite), скачанные документы
└── pyproject.toml
```

Подробная карта — модули, акторы, связи фич — в
[ARCHITECTURE.md](ARCHITECTURE.md).

## Модель данных

16 таблиц; ER-диаграмма и группировка — [ARCHITECTURE.md](ARCHITECTURE.md), § 3.
Ядро: `organizations` (заказчик/организатор/поставщик — одно лицо),
`announcements` → `lots` (id с сайта как PK) → `lot_status_history`,
`documents`, `contracts`, `lot_bids` (заявки участников с ценами).
LLM: `lot_analyses` (1:1 к лоту, идемпотентность по паре
`(analyzer_version, tz_sha256)`), `llm_calls` (учёт токенов).
Пользователи: `users` (scope), `user_queries` → `user_lot_matches` (кеш
семантического подбора). Операционные: `presets`, `scrape_runs`.
Автоподача: `client_credentials`, `submissions` (секреты и цены шифруются).

## Ограничения и known issues

1. **BIN заказчика не показан** в листинге и табе `lots` на goszakup — только в
   `general`-табе для организатора. Поэтому `Lot.customer` обычно без БИН, и
   агрегация на странице заказчиков идёт по названию (риск дублей орфографии).
2. **За кнопкой «Перейти» (`actionModalShowFiles`) теперь качаем** —
   через ajax-эндпоинт `actionAjaxModalShowFiles/{anno}/{file_type}`
   (анонимно, GET, HTML-таблица со ссылками на `v3bl.goszakup.gov.kz`).
   Намеренно тянем только тех.спецификации и конкурсную документацию
   (предикат `is_tz_like_name`), а не все 12+ приложений объявления.
3. **Лимит выдачи листинга 10 000 строк** — для preset'а, который попадает в
   этот лимит, нужно сузить диапазон суммы.
4. **Повторное сканирование закрытых лотов отключено** — по продуктовому
   решению. Если лот ушёл в Состоялась/Отменён, его карточка не перечитывается,
   договор может остаться непроставленным. Можно включить отдельным preset'ом
   с `status_codes=[360, 370, 410, 430]`.
5. **LLM-анализ обычно `confidence='high'`** — ТЗ скачивается, текст
   уходит в LLM. `low` остаётся только для пограничных случаев: у
   объявления нет файла «техническая спецификация / конкурсная
   документация», формат `.doc` (legacy, не парсим), битый PDF без
   текстового слоя. Тогда модель классифицирует по названию + ENSTRU.

## Конфигурация через переменные окружения

| Переменная | Значение по умолчанию | Назначение |
|---|---|---|
| `GZ_NO_AUTH` | (не задано) | Если `1` — выключает логин, работа под синтетическим админом (только dev) |
| `GZ_USER` / `GZ_PASSWORD` | `admin` / `admin` | Сид первого админа при пустой таблице `users` (не Basic Auth) |
| `GZ_SECRET_KEY` | (не задано) | Подпись cookie-сессии; на проде обязателен |
| `GZ_DATABASE_URL` | (не задано → SQLite) | `postgresql+psycopg://...` для Postgres |
| `GZ_OWS_TOKEN` | (не задано → HTML-путь) | Bearer-токен официального API OWS |
| `GZ_REDIS_URL` | `redis://localhost:6379/0` | Redis для Dramatiq-очередей и rate-limit |
| `CEREBRAS_API_KEY` | (не задано) | Включает LLM-классификацию ТЗ через Cerebras Inference. Без ключа — пайплайн работает, просто без LLM-шага. |
| `GZ_LLM_MODEL` | `gpt-oss-120b` | ID модели у Cerebras. Дефолт — open-weights gpt-oss-120b с tool calling + constrained decoding. |
| `GZ_TELEGRAM_BOT_TOKEN` | (не задано) | Бот уведомлений о новых матчах; без него уведомления тихо выключены |

Полный список (Telegram-вебхук, health-check, автоподача) — в
[CLAUDE.md](CLAUDE.md), раздел «Окружение».

## Лицензия и этика скрейпинга

Парсер реализует `Crawl-delay: 5s` из robots.txt goszakup на уровне сессии.
Не делайте параллельные прогоны с одного IP — это нарушит выполнение этого
ограничения. Если требуется ускорить — увеличивайте `count_record` или
сужайте фильтры, но не уменьшайте delay.
