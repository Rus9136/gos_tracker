# Goszakup Tracker

Локальный трекер тендеров с [goszakup.gov.kz](https://goszakup.gov.kz): ежедневный
скрейпинг по preset'ам, история статусов, загрузка документов, веб-интерфейс
с фильтрами и отчёты по заказчикам.

## Возможности

- 20 preset'ов из коробки — по одному на каждый регион Казахстана. Минимальная
  сумма лота 500 000 ₸ (зашита в `config.py`).
- Парсер уважает `Crawl-delay: 5s` из robots.txt goszakup.
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
- Веб-интерфейс: дашборд, актуальные / прошедшие тендеры с фильтрами (включая
  тип разработки и риск заточки), карточка лота со скачиванием документов и
  блоком LLM-анализа, отчёт по заказчикам/организаторам, журнал прогонов.
- launchd-агент для ежедневного запуска в 06:00.

## Требования

- macOS / Linux
- Python 3.11+
- ~300 МБ места под БД и документы (растёт с количеством отслеживаемых лотов)

## Установка

```bash
cd /Users/rus/Projects/goszakup
python3.13 -m venv .venv
.venv/bin/pip install -e .

# Создать таблицы и 20 дефолтных preset'ов
.venv/bin/python -m goszakup.cli init
.venv/bin/python -m goszakup.cli seed-presets
```

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

# Прогнать все активные preset'ы (то, что вызывается из launchd)
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
# Без авторизации (для локальной работы)
GZ_NO_AUTH=1 .venv/bin/python -m uvicorn goszakup.web.app:app --port 8765

# С HTTP Basic auth
GZ_USER=admin GZ_PASSWORD=секрет \
    .venv/bin/python -m uvicorn goszakup.web.app:app --port 8765
```

Открыть [http://127.0.0.1:8765](http://127.0.0.1:8765).

Маршруты:

| URL | Что показывает |
|---|---|
| `/` | Дашборд: счётчики, сводка по регионам и категориям, последние прогоны |
| `/actual` | Актуальные тендеры с фильтрами (регион, IT-категория, **тип разработки**, **риск заточки**, диапазон суммы) |
| `/past` | Прошедшие тендеры (Состоялась/Не состоялась/Отменён/Отказ) с теми же фильтрами |
| `/lot/{id}` | Карточка лота: поля, **LLM-анализ ТЗ**, история статусов, документы (скачивание), договор |
| `/organizations` | Заказчики/организаторы с агрегатами (кол-во лотов, сумма) |
| `/organization/{id}` | Все лоты конкретной организации |
| `/presets` | Список preset'ов, кнопка toggle active |
| `/runs` | Журнал прогонов парсера |
| `/document/{id}/download` | Отдаёт локально сохранённый файл |

### Ежедневный запуск через launchd

```bash
bash scripts/install_launchd.sh
# проверить:
launchctl list | grep goszakup
# вручную запустить вне расписания:
launchctl start com.user.goszakup.daily
# снять с авто:
launchctl unload ~/Library/LaunchAgents/com.user.goszakup.daily.plist
```

Логи: `data/logs/daily-YYYYmmdd-HHMMSS.log`.

## Структура

```
goszakup/
├── src/goszakup/
│   ├── config.py            # пути, MIN_AMOUNT, CRAWL_DELAY
│   ├── cli.py               # точка входа Typer
│   ├── db/
│   │   ├── models.py        # SQLAlchemy 2.0 модели
│   │   └── engine.py        # engine + SessionLocal + init_db
│   ├── scraper/
│   │   ├── http.py          # ThrottledSession (5s delay)
│   │   ├── search.py        # iter_listing(SearchParams) — табличная выдача
│   │   ├── announce.py      # детальная карточка объявления (4 таба)
│   │   ├── documents.py     # загрузка файлов с sha256-дедупом
│   │   ├── modal_files.py   # разворот «Перейти» через ajax + is_tz_like_name
│   │   ├── katos.py         # 20 регионов РК
│   │   └── statuses.py      # 25 кодов статусов, группы ACTUAL/PAST
│   ├── classify/
│   │   ├── it.py            # IT pre-filter (enstru exact + keyword)
│   │   └── llm.py           # LLM-классификация ТЗ (Claude tool use)
│   ├── jobs/
│   │   ├── seed.py          # дефолтные preset'ы по регионам
│   │   ├── run_preset.py    # пайплайн listing → diff → details → upsert
│   │   └── daily.py         # обход всех активных preset'ов
│   └── web/
│       ├── app.py           # FastAPI с роутами
│       ├── auth.py          # HTTP Basic (одна учётка)
│       ├── deps.py          # get_db, форматтеры
│       └── templates/       # Jinja2 (_layout, index, lots, lot, ...)
├── data/
│   ├── goszakup.sqlite      # БД
│   ├── docs/{anno_id}/...   # скачанные документы
│   └── logs/                # логи launchd-прогонов
├── scripts/
│   ├── run_daily.sh
│   ├── com.user.goszakup.daily.plist
│   └── install_launchd.sh
└── pyproject.toml
```

## Модель данных

- **organizations** — заказчики, организаторы и поставщики в одной таблице.
  Уникальность по БИН, если он есть; иначе по названию.
- **announcements** — объявления (anno_id с сайта как pk).
- **lots** — лоты внутри объявления (lot_id с сайта как pk).
- **lot_status_history** — запись на каждое изменение `status_code`.
- **documents** — скачанные файлы (sha256, локальный путь). Уникальность по
  паре `(announcement_id, url)`.
- **contracts** — договоры по лоту (появляются на финальных стадиях).
- **lot_analyses** — LLM-классификация лота 1:1: `dev_category`, `tech_stack`,
  `tz_summary`, `solo_feasible`, `vendor_lock_risk`, `analysis_confidence`,
  `analyzer_version`, `tz_sha256`. Идемпотентность: пара
  (analyzer_version, tz_sha256) — пропуск переанализа.
- **presets** — именованные наборы фильтров.
- **scrape_runs** — журнал прогонов парсера (вкл. счётчик `llm_analyzed`).

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
   уходит в Claude. `low` остаётся только для пограничных случаев: у
   объявления нет файла «техническая спецификация / конкурсная
   документация», формат `.doc` (legacy, не парсим), битый PDF без
   текстового слоя. Тогда модель классифицирует по названию + ENSTRU.

## Конфигурация через переменные окружения

| Переменная | Значение по умолчанию | Назначение |
|---|---|---|
| `GZ_NO_AUTH` | (не задано) | Если `1` — отключает HTTP Basic в веб-интерфейсе |
| `GZ_USER` | `admin` | Логин для HTTP Basic |
| `GZ_PASSWORD` | `admin` | Пароль для HTTP Basic |
| `CEREBRAS_API_KEY` | (не задано) | Включает LLM-классификацию ТЗ через Cerebras Inference. Без ключа — пайплайн работает, просто без LLM-шага. |
| `GZ_LLM_MODEL` | `gpt-oss-120b` | ID модели у Cerebras. Дефолт — open-weights gpt-oss-120b с tool calling + constrained decoding. |

## Лицензия и этика скрейпинга

Парсер реализует `Crawl-delay: 5s` из robots.txt goszakup на уровне сессии.
Не делайте параллельные прогоны с одного IP — это нарушит выполнение этого
ограничения. Если требуется ускорить — увеличивайте `count_record` или
сужайте фильтры, но не уменьшайте delay.
