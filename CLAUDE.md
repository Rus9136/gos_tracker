# CLAUDE.md

Контекст для Claude Code при работе с этим репозиторием.

## Что это за проект

Трекер тендеров с goszakup.gov.kz. Скрейпит ежедневно по preset'ам
(один на каждый из 20 регионов РК), хранит в SQLite, отдаёт FastAPI UI с
фильтрами и отчётами. Данные общие (один скрейпинг на всех), но вход —
**multi-user**: таблица `users`, форма `/login` + cookie-сессия, у каждого
пользователя свой read-time **scope** (регионы / IT-категории / мин. сумма).
Админ видит всё и управляет пользователями. Развёрнут на
<https://gost.salemsoft.kz> (Linux + systemd + nginx, см. ниже раздел
«Продакшн»). Может запускаться и под macOS как dev-окружение — launchd-агент
для этого случая лежит в `scripts/`.

См. README.md для обзора возможностей и команд.

## Окружение и базовые команды

- Python: `.venv/bin/python` (создаётся через `python3.13 -m venv .venv`). Не
  использовать системный `/usr/bin/python3` — там 3.9, который не понимает
  `float | None`.
- Установка: `.venv/bin/pip install -e .`
- Запуск CLI: `.venv/bin/python -m goszakup.cli ...`
- Запуск UI: `GZ_NO_AUTH=1 .venv/bin/python -m uvicorn goszakup.web.app:app --port 8765`
- БД: **Postgres** (на проде системный PG-15, локально — docker compose PG-16,
  CI — PG-16). На dev-машине без `GZ_DATABASE_URL` остаётся **SQLite**
  `data/goszakup.sqlite` (WAL-режим, WAL/SHM-файлы — норма, в git не пушить).
  Выбор диалекта — через `GZ_DATABASE_URL`. `db/engine.py` сам определяет
  диалект и применяет нужные настройки (`timeout`/WAL для SQLite, `pool_pre_ping`
  для Postgres).
- **Docker (Phase 1)**: `docker compose up -d` поднимает postgres + uvicorn.
  Web слушает на `127.0.0.1:8766` (8765 на этой машине занят systemd-сервисом
  goszakup-web). Образ multi-stage, ~400 МБ. Шаблоны/статика попадают в wheel
  через `[tool.setuptools.package-data]` — без этого uvicorn падает на
  `mount("/static")`. Внутри контейнера обязателен `GZ_DATA_DIR=/app/data`
  (иначе config попытается писать в site-packages).
- **Migration data**: `scripts/migrate_sqlite_to_pg.py` — копирует данные
  SQLite → Postgres с stub-Announcement для orphan FK-ссылок. ON CONFLICT
  DO NOTHING делает идемпотентным. `_bump_sequences()` поднимает PG-sequences
  до max(id) — иначе следующий INSERT без id наступит на занятые номера.
- Миграции: **Alembic** (`migrations/`, env берёт URL из `config.DB_URL`).
  Workflow при правке `db/models.py`:
  1) `.venv/bin/alembic revision --autogenerate -m "что меняем"`,
  2) глазами проверить сгенерированный файл в `migrations/versions/`,
  3) `.venv/bin/alembic upgrade head` локально, прогнать pytest,
  4) коммит. На проде — `alembic upgrade head` перед `systemctl restart`.
  `init_db()`/`create_all()` остались как safety net (идемпотентно) — но
  единственная истинная схема теперь в миграциях.
- ENV: подгружается из `./env` через `python-dotenv` (вызов в `config.py`,
  `override=False` — реальный shell-env приоритетнее). Обязательная переменная
  для LLM/чата — `CEREBRAS_API_KEY`. Аутентификация (см. раздел «Пользователи»):
  **`GZ_SECRET_KEY`** (подпись cookie-сессии — на проде обязателен, иначе
  при рестарте все сессии слетают), `GZ_USER`/`GZ_PASSWORD` (теперь — только
  **сид первого админа** при пустой таблице `users`, не Basic Auth),
  `GZ_NO_AUTH=1` (выключает логин и работает под синтетическим админом —
  только для dev-машины, на проде НЕ ставить). Опционально: `GZ_LLM_MODEL`
  (дефолт `gpt-oss-120b`), **`GZ_REDIS_URL`** (Phase 3, дефолт
  `redis://localhost:6379/0`), **`GZ_TELEGRAM_BOT_TOKEN`** (бот для
  уведомлений о новых матчах, правило #18; без него уведомления тихо
  выключены), `GZ_TELEGRAM_WEBHOOK_SECRET` (секрет вебхука кнопки
  «Подробнее» в уведомлении; без него `POST /telegram/webhook` отвечает 503;
  после добавления один раз выполнить `cli telegram-set-webhook`),
  `GZ_PUBLIC_BASE_URL` (адрес UI для ссылки в уведомлении,
  дефолт `https://gost.salemsoft.kz`). Сторож LLM-контура (правило #20):
  `GZ_HEALTH_MATCH_STALE_HOURS` (порог давности матча для тревоги, дефолт 48;
  `0` — выключить эту проверку), `GZ_HEALTH_ALERT_COOLDOWN` (пауза между
  повторными алертами, дефолт 21600 = 6ч). Автоподача заявок (правило #19,
  `TENDER_AUTOSUBMIT_PLAN.md`): **`GZ_VAULT_MASTER_KEY`** (base64 от 32 байт —
  мастер-ключ KeyVault для чужих .p12/паролей/PIN; без него обращение к Vault
  падает), `GZ_AUTOSUBMIT_AGENT_URL` (адрес Windows submit-agent по приватной
  сети; без него диспетчер автоподачи выключен), `GZ_AUTOSUBMIT_WARMUP_LEAD`
  (за сколько секунд до open_at слать агенту задачу на прогрев, дефолт 300),
  `GZ_AUTOSUBMIT_INGEST_TOKEN` (общий токен для `POST /autosubmit/result` —
  агент шлёт сюда RunResult; машинная авторизация, без него ingest выключен),
  `GZ_AUTOSUBMIT_AGENT_TOKEN` (токен Linux→agent для `POST /run`, заголовок
  `X-Agent-Token`; должен совпасть с `GZ_AGENT_TOKEN` на Windows-узле).
- **Очередь задач (Phase 3)**: Dramatiq + Redis. Пайплайн разбит на 3
  стадии — `listing_actor` (одна выдача), `detail_actor` (одно объявление,
  4 таба + документы), `analyze_actor` (LLM по одному лоту). `daily_actor`
  — ежедневный entry-point. `ingest_actor` — ad-hoc по БИН.
  **`scan_actor`** — ad-hoc по произвольным kato/amount/status/IT-категориям
  из UI (`/scan`); умеет режимы `listing_only` / без LLM-документов / полный
  через флаги `with_docs`, `with_llm` (передаются дальше в `detail_actor`).
  Очереди именованы:
  `goszakup_daily`, `goszakup_listing`, `goszakup_detail`, `goszakup_llm`.
  **`expire_actor`** — ежечасный (systemd `goszakup-expire.timer`) сброс
  `is_actual=False` у лотов с истёкшим сроком приёма заявок
  (`Announcement.application_end < now()`). К goszakup НЕ ходит — только
  локальный UPDATE по БД (см. `jobs/expire.py`). Очередь `goszakup_daily`.
  Cross-process rate-limit на goszakup — `RedisThrottledSession` (SET NX EX
  с TTL=5s, global mutex). `ScrapeRun.finished_at` закрывается, когда
  Redis-counter `goszakup:run:<id>:pending` достигает нуля.

## Продакшн: gost.salemsoft.kz

Развёрнут на VPS (Ubuntu 24.04, nginx 1.24, certbot 2.9). Не путать с
`DEPLOY.md` — там общий рецепт для чистого Ubuntu (`/opt/goszakup`, юзер
`goszakup`). На реальном сервере конвенция другая (как у соседних
`docs.salemsoft.kz`, `pro.salemsoft.kz`, …): все проекты живут под
`/home/rus/projects/` от юзера `rus`.

- **URL**: <https://gost.salemsoft.kz> (HTTPS, вход по форме `/login`).
- **Каталог**: `/home/rus/projects/gos_tracker`, venv в `.venv` (python3.12 —
  единственный на сервере, 3.11/3.13 нет, проекту хватает: `requires-python = ">=3.11"`).
- **БД**: Postgres 15 (`goszakup_prod` в системном кластере на
  `127.0.0.1:5432`, рядом с `salem_docs_prod`/`businesscamp`). Cutover
  с SQLite — 2026-05-20, downtime ~95с. SQLite-файлы `data/goszakup.sqlite*`
  оставлены на диске как фолбэк; для отката закомментировать `GZ_DATABASE_URL`
  в `.env` и `sudo systemctl restart goszakup-web.service`.
- **`.env`**: лежит в корне, `chmod 600`. Содержит `CEREBRAS_API_KEY`,
  `GZ_USER`, `GZ_PASSWORD` (сид первого админа), **`GZ_SECRET_KEY`** (подпись
  cookie-сессии), **`GZ_DATABASE_URL`** (postgresql+psycopg://...).
  `GZ_NO_AUTH` **НЕ** выставлен — логин по форме обязателен. Не пушить.
- **Сервисы systemd** (юзер `rus`, не `goszakup`/`www-data`):
  - `goszakup-web.service` — uvicorn на `127.0.0.1:8765`,
    `ProtectSystem=strict`, `ReadWritePaths=/home/rus/projects/gos_tracker/data`.
    EnvironmentFile НЕ используется — `.env` подхватывается через
    `python-dotenv` в `config.py`.
  - `goszakup-daily.timer` + `goszakup-daily.service` —
    `OnCalendar=*-*-* 06:00:00`, `RandomizedDelaySec=300`, oneshot. Запускает
    `cli daily` — после Phase 3 это **enqueue** `daily_actor.send()` в Dramatiq
    (за ~миллисекунды), затем выходит. Реальную работу делает worker. Lock-файл
    `data/.daily.lock` больше не нужен, но в .service всё ещё есть
    `ExecStartPre`/`ExecStopPost` — это лишний, но безвредный артефакт.
    Чтобы вернуться на старый синхронный режим — `cli daily --sync`.
  - **`goszakup-expire.timer` + `goszakup-expire.service`** —
    `OnCalendar=*-*-* *:05:00` (ежечасно), oneshot. Делает `cli expire` →
    enqueue `expire_actor` → worker гасит `is_actual` у лотов с истёкшим
    сроком приёма заявок. Шаблоны юнитов — в `scripts/systemd/`. К goszakup
    не ходит, lock/rate-limit не нужны. См. правило #12.
  - **`goszakup-health.timer` + `goszakup-health.service`** (правило #20,
    шаблоны в `scripts/systemd/`) — `OnCalendar=*-*-* *:35:00` (ежечасно),
    oneshot. Делает `cli health-check`: пингует Cerebras и смотрит давность
    матчей, при поломке шлёт алерт админам в Telegram. К goszakup и в очередь
    не ходит. Ставится один раз:
    `sudo cp scripts/systemd/goszakup-health.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now goszakup-health.timer`.
  - **`goszakup-autosubmit.timer` + `goszakup-autosubmit.service`**
    (правило #19, шаблоны в `scripts/systemd/`) — `OnCalendar=minutely`, oneshot.
    Делает `cli autosubmit-dispatch --enqueue` → `autosubmit_dispatch_actor`
    (очередь `goszakup_autosubmit`) шлёт Windows submit-agent'у PLANNED-подачи,
    открывающиеся в ближайший warmup-lead. Без `GZ_AUTOSUBMIT_AGENT_URL` актор
    тихо выходит. Включать только когда Windows-agent (Phase 2) готов.
  - **`goszakup-worker.service`** (Phase 3, задеплоен 2026-05-20) —
    `dramatiq goszakup.queue.actors -p 2 -t 4`. Подключается к выделенному
    Redis-контейнеру `goszakup-redis` на `127.0.0.1:6380`. `ProtectHome=true`
    НЕ ставить — venv в `/home/rus/...` иначе exec даст 203.
    ВАЖНО: unit перечисляет очереди ЯВНО через `--queues` — при добавлении
    нового actor'а с новой очередью её надо дописать и в unit (live +
    шаблон `scripts/systemd/`), иначе задачи молча копятся в Redis.
    Текущий список: daily, listing, detail, llm, matching, notify,
    autosubmit (все с префиксом `goszakup_`).
  - **`goszakup-redis` Docker-контейнер** (отдельный от хостового
    `shared-redis` на 6379, мы к нему не подключаемся — у него auth и его
    используют другие проекты). Запущен через `docker run -d --name
    goszakup-redis --restart unless-stopped -p 127.0.0.1:6380:6379
    redis:7-alpine redis-server --save "" --appendonly no`. AOF off —
    очередь и rate-limit персистентность не требуют.
- **nginx**: `/etc/nginx/sites-available/gost.salemsoft.kz.conf` (симлинк в
  `sites-enabled/`). Паттерн как у соседних `*.salemsoft.kz`: HTTP→HTTPS
  redirect + webroot для ACME (`/var/www/certbot`), HTTPS-блок с
  `proxy_pass http://127.0.0.1:8765`, security-заголовки (HSTS, XFO и т.д.),
  `proxy_read_timeout 600s` — это критично, потому что ручные «Загрузить
  документы» / «Переанализировать» на /lot/{id} идут десятки секунд.
- **SSL**: Let's Encrypt через webroot. Авто-продление выполняет общий
  `certbot.timer` сервера (он уже стоял до этого деплоя для других доменов);
  отдельный таймер заводить не надо.

### Обновление кода на продакшне

```bash
cd /home/rus/projects/gos_tracker
git pull --ff-only
./.venv/bin/pip install -e .                  # только если pyproject.toml менялся
sudo systemctl restart goszakup-web.service
sudo systemctl restart goszakup-worker.service  # Phase 3, если уже задеплоен
# goszakup-daily.timer перезагружать не нужно — следующий oneshot
# подхватит свежий код сам.
```

При схема-изменениях на проде — сначала `alembic upgrade head`, потом
`systemctl restart goszakup-web`. `create_all()` в `init_db()` оставлен
как safety net, но он не делает ALTER — реальные миграции идут через
Alembic. Если в релизе бампнули `ANALYZER_VERSION` — следующий `daily`
сам перегонит лоты со старой версией анализа (правило #8).

**Релиз multi-user-входа (одноразово):** в `.env` добавить `GZ_SECRET_KEY`
(длинный случайный, напр. `openssl rand -hex 32`); `alembic upgrade head`
создаст таблицу `users`; при первом старте `goszakup-web` `seed_admin_from_env`
заведёт первого админа из имеющихся `GZ_USER`/`GZ_PASSWORD` (если `users`
пуста). Дальше пользователи заводятся через UI `/users` или
`cli create-user --admin`. После деплоя Basic Auth больше нет — вход по
форме `/login`.

### Полезные команды на сервере

```bash
sudo journalctl -u goszakup-web -f                  # лог UI
sudo journalctl -u goszakup-daily --since today     # последний прогон
sudo systemctl start goszakup-daily.service         # ручной запуск вне 06:00
systemctl list-timers goszakup-daily.timer          # когда сработает в след. раз
sudo tail -f /var/log/nginx/gost.salemsoft_error.log

# PG-консоль на проде (пароль в .env, GZ_DATABASE_URL)
PGPASSWORD=$(grep '^GZ_DATABASE_URL=' .env | sed -E 's|.*//goszakup:([^@]+)@.*|\1|') \
  psql -h 127.0.0.1 -p 5432 -U goszakup -d goszakup_prod

# Откат на SQLite (если в PG что-то сломалось):
# 1. закомментировать GZ_DATABASE_URL= в .env
# 2. sudo systemctl restart goszakup-web.service
# Данные в SQLite не трогали с момента cutover — там стейт «как было».
```

`data/logs/launchd-*.log` — артефакты mac-окружения, на сервере не пишутся
(вывод идёт в journal).

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

10. **На проде Postgres, на dev-fallback SQLite в WAL** (см. `db/engine.py`,
    `make_url(DB_URL).get_backend_name()` ветвление). Для SQLite:
    `connect_args={"timeout": 30}` + event listener `_sqlite_pragmas`
    (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL`) — иначе uvicorn
    (читатель) и `daily`/`reanalyze` (писатели) валятся с `database is locked`.
    Для Postgres — стандартный pool с `pool_pre_ping=True` (отлавливает
    оборванные соединения после рестарта PG). Правило «не запускать `daily`
    параллельно с `run-preset`» — теперь больше про rate-limit на goszakup,
    а не про блокировки БД (PG разруливает параллельных писателей).

11. **FK на `announcements.id` в Postgres строгий** — в SQLite по умолчанию
    не enforced, поэтому раньше можно было создать Lot со ссылкой на
    несуществующее Announcement (stub-лот). На PG это IntegrityError.
    `jobs/run_preset._ensure_announcement_stub` создаёт stub-Announcement
    (только id + url) до insert'а Lot, `_save_announcement` потом дополняет
    реальными полями. Без этой страховки prod-daily валится на любом новом
    лоте при пустой `announcements`.

12. **«Актуальность» лота = `Lot.is_actual` (флаг), единый источник правды.**
    Флаг выставляется из `status_code` (`ACTUAL_STATUSES`) в
    `_upsert_lot_from_listing` И дополнительно гасится по факту истечения срока
    приёма заявок: goszakup не всегда меняет статус сразу после дедлайна.
    Дедлайн (`Announcement.application_end`, хранится в UTC) парсится в
    `_parse_general` (`_find_deadline` — матч по подстроке «окончан…заяв/ценов»,
    т.к. точное название поля плавает по способу закупки;
    `_parse_deadline` локализует время как Алматы UTC+5 → UTC). Снятие —
    `jobs/expire.expire_actual_lots` (bulk UPDATE), запускается ежечасно
    `expire_actor`/`goszakup-expire.timer` и в начале `daily_actor`. Лот из БД
    НЕ удаляется, только флаг. НЕ добавлять read-time фильтр по дедлайну в
    `web/app._lots_query` — иначе истёкший лот с флагом=true не попадёт ни в
    `/actual`, ни в `/past` (рассинхрон); полагаемся на флаг, лаг ≤1ч.
    Поле новое: у лотов до этой фичи `application_end` пустой и крон их не
    тронет — разовый бэкофилл `scripts/backfill_deadlines.py`.

13. **`_call_llm` ретраит Cerebras 429 «queue_exceeded»** с бэкоффом 5/15/30с
    (3 ретрая, см. `_RETRY_DELAYS`). Это не «новая фича», а лечение
    burst-throttling: free-tier Cerebras душит при >30 req/мин. В массовых
    скриптах (`scripts/reanalyze_actual_it.py`) дополнительно ставится
    1.5с pacing между лотами — он держит темп под лимитом, чтобы ретрай
    вообще редко срабатывал.

14. **Закрытие `ScrapeRun.finished_at` подстраховано БД-heartbeat'ом.**
    Штатно run закрывает Redis-pending-счётчик (`goszakup:run:<id>:pending`,
    DECR в `detail_actor`). Но `goszakup-redis` непёрсистентный (`--save ""
    --appendonly no`) — рестарт контейнера/воркера (например, при деплое)
    теряет счётчик и очередь detail-тасок, декрементить больше некому, и
    `finished_at` навсегда остаётся NULL. Тогда UI («идёт прогон #N» на
    `/scan`, `/ingest`) висит вечно. Поэтому каждый шаг пайплайна бьёт
    `ScrapeRun.last_progress_at` (`queue/actors._touch_run` + инкременты
    счётчиков; на долгом listing-проходе — каждые 100 строк), а
    `jobs/ingest.close_stale_runs` закрывает прогоны без прогресса дольше
    `_STALE_RUN_AFTER` (**15 мин бездействия**, НЕ возраста — «весь РК»-скан
    идёт часами и жив). Reaper зовётся лениво из `find_active_run` (UI),
    из `/runs` и фоном из `expire_actor` (ежечасно). Живой прогон бьёт
    heartbeat каждые секунды — 15 мин тишины надёжно означают «мёртв».
    НЕ возвращать staleness обратно «по `started_at`» — иначе либо
    зомби-прогон блокирует UI до 2ч, либо живой долгий скан реапнется.

15. **Multi-user поверх общих данных — изоляция read-time, НЕ на уровне БД.**
    Лоты в БД одни на всех (скрейпинг глобальный, по preset'ам/регионам).
    «Scope» пользователя (`User.regions`/`it_categories`/`min_amount`) — это
    фильтр на чтение: `web/app._scope_conditions` добавляет `WHERE` к выборкам
    лотов на `/actual`, `/past`, `/starred`, дашборде и в счётчиках сайдбара;
    `_lot_in_scope` гейтит `/lot/{id}` (вне scope → 404). Админ
    (`User.is_admin`) и dev-аноним (`GZ_NO_AUTH=1`) видят всё — пустой список
    условий. НЕ привязывать лоты к пользователям через FK/M2M и НЕ трогать
    пайплайн: scope живёт только в web-слое. Пустой `regions`/`it_categories`
    = «без ограничения по измерению», поэтому проверки `if user.regions:`
    (а не `is not None`). Кеш счётчиков сайдбара (`_nav_cache`) ключуется по
    `uid` — у каждого scope свой, общий кеш дал бы протечку чисел.

16. **Аутентификация — таблица `users`, НЕ env Basic Auth.** Пароли —
    bcrypt в `User.password_hash`; bcrypt вызывается напрямую (passlib 1.7.4
    несовместим с bcrypt 5.x) с обрезкой пароля до 72 байт. Вход — форма
    `/login`, идентичность в подписанной cookie (`SessionMiddleware`,
    `GZ_SECRET_KEY`; `request.session["uid"]`). Зависимости роутов:
    `require_user` (нет сессии → `NotAuthenticated` → редирект на `/login`),
    `require_admin` (403 не-админу) — системные страницы (`/presets`, `/scan`,
    `/runs`, `/ingest`, `/users`) и goszakup-мутирующие POST'ы
    (`fetch_documents`, `analyze`) только для админа. `GZ_USER`/`GZ_PASSWORD`
    теперь лишь **сид первого админа** через `seed_admin_from_env` (lifespan,
    срабатывает только при пустой `users`). Бутстрап из консоли —
    `cli create-user --admin`.

17. **Семантический подбор лотов (`UserQuery`/`UserLotMatch`) — кеш, LLM на
    UI не вызывается.** Пользователь пишет NL-запрос «какие лоты хочу»
    (`/queries`); матч считается LLM-ом против `LotAnalysis.tz_summary`
    (НЕ против PDF — дорого) и кешируется в `UserLotMatch`; `/matched` —
    чистый SQL по кешу. Идемпотентность пары (query, lot) — по
    `(query_version, matcher_version)` ПЛЮС свежесть анализа: переанализ лота
    (`analyzed_at` новее `matched_at`) инвалидирует матч автоматически.
    Правка текста запроса → `UserQuery.version += 1` → пересчёт upsert'ом.
    Fan-out: новый анализ → `enqueue_matches_for_lot` (все три пути:
    `analyze_actor`, sync `run_preset`, кнопка «Переанализировать») с
    pre-filter по scope (`goszakup.scope.lot_in_scope`) ДО постановки в
    очередь `goszakup_matching`; создание/правка запроса → backfill
    (`jobs/match.py`, CLI `match-backfill`). На ошибке LLM запись НЕ
    создаётся (пара переанализируется следующим прогоном). При правках
    промпта/схемы матчера бампать `MATCHER_VERSION` в `classify/matcher.py`.
    Dev-нюанс: GZ_NO_AUTH-админ имеет id=0 без строки в `users` — поэтому
    fan-out использует outerjoin, а backfill терпит отсутствующего владельца
    (пустой scope = видит всё).

18. **Telegram-уведомления о новых матчах — ТОЛЬКО на forward-потоке, не на
    backfill.** Когда лот проанализирован и положительно сматчился
    (`enqueue_matches_for_lot` → `match_actor(..., notify=True)`), ставится
    `notify_actor` (очередь `goszakup_notify`), который шлёт сообщение в
    Telegram владельцу запроса. Backfill (создание/правка запроса, кнопка
    «Подобрать сейчас») зовёт `match_actor(notify=False)` — иначе при первом
    запросе пользователю прилетела бы пачка по всем старым лотам. Дедуп — по
    `UserLotMatch.notified_at` (NULL = не слали): actor идемпотентен,
    переанализ лота не шлёт повторно. Не уходит, если `User.notify_telegram`
    выключен / нет `User.telegram_chat_id` / нет `GZ_TELEGRAM_BOT_TOKEN` (в
    этих случаях `notified_at` всё равно проставляется, чтобы не дёргать
    повторно). Отправка (`notify/telegram.py`) defensive — не валит матчинг
    (правило #7). chat_id пользователь сохраняет сам на `/settings` (ручной
    ввод numeric id от @userinfobot, есть кнопка «Отправить тест»). Один
    общий бот на сервис (`GZ_TELEGRAM_BOT_TOKEN` в `.env`). Очередь
    `goszakup_notify` обязана быть в `--queues` воркера (см. выше).
    **Кнопка «Подробнее о лоте»** в уведомлении — inline-callback →
    `POST /telegram/webhook` (машинная авторизация по
    `X-Telegram-Bot-Api-Secret-Token` = `GZ_TELEGRAM_WEBHOOK_SECRET`,
    constant-time; без секрета — 503) → `explain_actor` (та же очередь
    `goszakup_notify`, `max_retries=0` — при ошибке LLM пользователю сразу
    уходит fallback-сообщение, ретрай дал бы дубли). Actor отвечает ТОЛЬКО
    известным `telegram_chat_id` (вебхук публичный, callback_data
    подделываемая), зовёт `classify/llm.explain_lot` (тот же контекст, что
    чат: поля лота + полный текст ТЗ) и шлёт объяснение простым языком в
    чат; расход пишется в LlmCall `kind="explain"`. Вебхук регистрируется
    одноразово: `cli telegram-set-webhook` (снять — `--drop`;
    `allowed_updates=["callback_query"]` — обычные сообщения боту не
    приходят).

19. **Автоподача заявок — sealed-bid через «золотой клиент», крипто НЕ headless.**
    Полный дизайн — `TENDER_AUTOSUBMIT_PLAN.md` + `TENDER_ECP_SIGNING_GUIDE.md`.
    Цель — открытые конкурсы; главный фактор — **скорость** («кто первым подал —
    выигрывает»). Установлено разведкой по HAR: (а) **пред-стейдж невозможен** —
    кнопка «Подать» гейтится до `time_open`, весь визард идёт в гонке после
    старта; (б) цена не подписывается, а **ГОСТ-шифруется** (sealed-bid конверт на
    сертификат тендера) нативным **Tumar CSP** — поле `sign` считается внутри
    закрытого CSP и оффлайн не реверсится. Поэтому крипто берём у эталонного
    Tumar на **Windows submit-agent** (path A′), а не реализуем headless. Linux-
    часть (`autosubmit/`, `vault/`, `queue/autosubmit.py`) — provider-agnostic:
    KeyVault (AES-256-GCM) для чужих `.p12`/паролей/PIN, диспетчер задач агенту к
    `open_at`, статус-машина `Submission`, «выстрел» финального POST на httpx.
    **Цена в БД ШИФРУЕТСЯ** (`Submission.bid_enc`) — sealed-bid секретность.
    Windows submit-agent (Phase 2) — **каркас в `agent/`** (отдельный деплой,
    httpx+playwright+pywinauto, НЕ тащит пакет goszakup): сервер `POST /run`,
    оркестрация прогрев→ожидание→визард→отчёт. «Сантехника» рабочая; UI-селекторы
    визарда / окно Tumar / NCALayer-логин помечены `TODO(recon)` — заполняются по
    живому конкурсу (`agent/RECON.md` + `agent/recon_dump.py`). Очередь
    `goszakup_autosubmit` обязана быть в `--queues` воркера.

20. **Отказ LLM молчалив — его ловит только активный сторож.** Правило #7
    намеренно гасит ошибки LLM (пайплайн не должен падать из-за провайдера),
    поэтому его отказ не виден нигде, кроме `log.warning`. 2026-06-30 Cerebras
    начал отдавать `402 payment_required` (кончилась квота) — матчинг и
    Telegram-уведомления молча стояли **15 дней**, пока пользователь не заметил
    сам. `jobs/health.py` + `cli health-check` + `goszakup-health.timer`
    (ежечасно, `:35`) дают сигнал: **живой пинг** Cerebras (~10 токенов) и
    давность последнего матча. НЕ выводить здоровье из «давно не было
    LLM-вызовов»: в тихий день новых IT-лотов может не быть вовсе, и такой
    признак врёт (29.06.2026 — легитимный ноль вызовов). Алерт идёт админам с
    `telegram_chat_id`, дедуп — Redis-ключ `goszakup:health:alerted` с TTL
    (`GZ_HEALTH_ALERT_COOLDOWN`), иначе за две недели прилетело бы 300+
    сообщений. Redis недоступен → шлём (лучше лишнее, чем тишина).
    Диагностика такого сбоя по БД: у актуального IT-лота **нет строки** в
    `lot_analyses` (при ошибке LLM запись не создаётся намеренно — чтобы
    следующий прогон переанализировал). `analyzer_version='rules-v1-ru'` —
    НЕ признак сбоя: rule-based идёт до LLM и экономит вызов при
    `confidence >= 0.85`.

21. **Источник данных — официальный API OWS с HTML-фолбэком, через
    абстракцию `DataSource`.** С 2026-07-24 листинг/детали/файлы идут через
    ows.goszakup.gov.kz (Bearer `GZ_OWS_TOKEN`, выдан ЦЭФ на год): GraphQL v3
    отдаёт объявление+лоты+файлы одним запросом вместо 6-8 HTML-страниц ×
    Crawl-delay 5с. Пайплайн работает с `sources.make_source(redis)`:
    токен есть → `FallbackSource(ApiSource, HtmlSource)`, нет → `HtmlSource`
    (прежний путь бит-в-бит). НЕ вызывать `iter_listing`/`fetch_announcement`/
    `download_document` из scraper/* напрямую в пайплайне — только через
    source. Ключевые факты (recon в `tests/fixtures/api/NOTES.md`): rate-limit
    API свой (`GZ_API_DELAY`=1с, Redis-ключ `goszakup:api_rate_limit`) и
    НЕЗАВИСИМ от Crawl-delay HTML; OWS доступен без KZ-туннеля (API-клиент
    ходит напрямую, `GZ_OWS_USE_PROXY=1` — закладка); даты API — алматинское
    время UTC+5; невалидный/истёкший токен = **404 «Invalid Route»**, не 401
    (ловится `OwsAuthError`); серверные фильтры — статусы/`amount:[от]`/
    customerBin, регион фильтруется клиентски по префиксу КАТО (2 цифры);
    ЕНС ТРУ-имя = `Lots.nameRu`, код — из `Plans[].RefEnstru` (у свежих ЗЦП
    плана нет → код добирает HTML-запрос subpriceoffer для IT-лотов). Каждый
    уход на фолбэк пишет WARNING + Redis-флаг `goszakup:api_degraded` — его
    видит health-check (плюс живой пинг OWS и предупреждение об истечении
    токена по `GZ_OWS_TOKEN_EXPIRES`). Откат: закомментировать `GZ_OWS_TOKEN`
    в `.env` + рестарт worker/web.
    **Daily с токеном — инкрементальный** (фаза 5, 2026-07-27): вместо 20
    региональных listing_actor'ов `daily_actor` шлёт `api_daily_actor` (один
    проход `Lots` по окну `lastUpdateDate`; статусы/мин.сумма — объединение
    активных preset'ов, `jobs/incremental.daily_scan_params`; регион лота —
    из точки поставки, `api/mapping.region_from_kato_list`) и
    `contracts_sync_actor` (см. ниже). Водяной знак — started_at последнего
    успешного прогона с note-тегом `api-daily`/`contracts-sync` минус 1ч
    (`jobs/incremental.sync_window`); потолок окна 7 дней — при простое
    дольше окно обрезается с WARNING, дозаполнять ручным прогоном preset'ов.
    Фолбэк: OwsApiError в api_daily → авто-fan-out прежних 20 listing_actor.
    Preset'ы остаются конфигурацией покрытия и ручным путём (/presets →
    «Запустить сейчас»). `_upsert_lot_from_listing` НЕ затирает `Lot.kato`
    пустым (persona-scope, правило #15). **Договоры/победители** приезжают
    из `contracts_sync_actor` (GraphQL `Contract`+`ContractUnits.lotId`,
    только для уже известных лотов, ВКЛЮЧАЯ закрытые — это закрывает пробел
    правила #5 и регрессию API-пути, где HTML-табы winners/contracts не
    читаются). `lot.winner_*` из договора не затирает добытое HTML-ом;
    статус договора — name_ru из `/v3/refs/ref_contract_status`. Ручной
    бэкофилл: `cli contracts-sync --days N [--sync]`. Уникальность
    `contracts (lot_id, contract_number)` — миграция 366463c4707d.

## Где что лежит

- `sources.py` — абстракция DataSource (правило #21): `HtmlSource` (обёртка
  над scraper/*), `ApiSource` (OWS), `FallbackSource`, `make_source`.
- `api/` — клиент официального API OWS: `client.py` (OwsClient: Bearer, свой
  rate-limit, GraphQL с курсорной пагинацией, `OwsAuthError` для 404-токена),
  `queries.py` (LISTING_QUERY/DETAIL_QUERY), `mapping.py` (JSON → dataclasses,
  таймзона UTC+5, КАТО-префиксы), `refs.py` (кэш справочников /v3/refs/*).
  Recon-факты и фикстуры — `tests/fixtures/api/NOTES.md`.
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
  Страница **`/scan`** (`scan.html`) — форма ad-hoc прогона: регион,
  amount, статусы, IT-категории, режим запуска (только листинг / без
  LLM-документов / полный). POST `/scan/run` зовёт
  `jobs.scan.create_scan_run` (ScrapeRun stub), затем `scan_actor.send(...)`
  → 303 на `/runs/{id}`. Эта же `find_active_run` блокирует параллельные
  запуски — чтобы не нарушить Crawl-delay.
  Аутентификация: `/login` (GET/POST), `/logout`, CRUD `/users` (admin).
  `_scope_conditions`/`_lot_in_scope` — persona-scope (правило #15).
- `web/auth.py` — bcrypt-хеши, `authenticate`, `get_current_user` из сессии,
  зависимости `require_user`/`require_admin`, `NotAuthenticated` (→ редирект
  на `/login`), `seed_admin_from_env` (сид первого админа из `GZ_USER`/
  `GZ_PASSWORD`). `GZ_NO_AUTH=1` → синтетический dev-админ. См. правило #16.
- `scraper/modal_files.py` — разворачивает кнопку «Перейти» через ajax,
  возвращает прямые ссылки на файлы на `v3bl`. Содержит публичный
  `is_tz_like_name(text)` — переиспользуется в `classify/llm.pick_tz_document`.
- `jobs/run_preset.py` — пайплайн. Двухфазный: сначала listing+upsert stub'ов,
  потом details для новых/сменивших статус. IT-фильтр применяется на фазе 1.
  В фазе 2 `_save_documents` сначала качает прямые `<a href>`, затем для
  строк за «Перейти» проверяет `is_tz_like_name` и разворачивает modal.
  LLM-шаг — следом, только для лотов с `it_category`. Флаг `listing_only`
  на `execute_search`/`run_preset` (и CLI `run-once --listing-only`)
  останавливается после фазы 1 — нужен для ad-hoc стабования по большим
  выборкам, когда details ходить дорого/нежелательно.
- `jobs/expire.py` — `expire_actual_lots(session)`: bulk-UPDATE `is_actual=False`
  для актуальных лотов с истёкшим `Announcement.application_end`. Источник
  правила #12. Бэкофилл для старых лотов — `scripts/backfill_deadlines.py`
  (тянет только таб `general`, по одному запросу на объявление).
- `jobs/health.py` — сторож LLM-контура (правило #20). `check_llm()` — живой
  пинг Cerebras; `match_age_hours()` — давность последнего матча;
  `run_health_check(session, notify=)` — собрать проблемы и разослать алерт
  админам. Точка входа — `cli health-check` (exit 1 при проблеме, чтобы юнит
  стал failed и это было видно в `systemctl list-units --failed` даже когда
  Telegram недоступен).
- `jobs/org_report.py` — отчёт по закупкам организации (`/organization/{id}/report`,
  admin-only, `?format=md` — выгрузка в Markdown). Чистый SQL по уже загруженным
  лотам, к goszakup не ходит. `related_org_ids` склеивает дубли организации
  (customer без БИН из листинга + organizer с БИН из деталей — БИН в таблице
  уникален, поэтому дубль всегда такой пары). Победители лотов приезжают из
  вкладки winners в detail-фазе (поля `Lot.winner_bin`/`winner_name`). Типовой
  сценарий: `/ingest` по БИН (услуги, годы, завершённые статусы) → дождаться
  прогона → кнопка «Отчёт по закупкам» на странице организации.
- `jobs/scan.py` — `create_scan_run(...)` для UI `/scan`. Собирает
  человекочитаемый `note` (регион, диапазон сумм, статусы, IT-категории,
  режим), проверяет `find_active_run`, создаёт `ScrapeRun(preset_id=NULL)`.
  Режимы (`MODE_LISTING` / `MODE_NO_HEAVY` / `MODE_FULL`) преобразуются в
  тройку флагов `(listing_only, with_docs, with_llm)` через `mode_flags`.
- `jobs/match.py` — `backfill_query(query_id, limit, sync)`: матч запроса по
  актуальным лотам в scope владельца (создание/правка запроса, CLI
  `match-backfill`). `--sync` — без Redis/воркера.
- `classify/matcher.py` — LLM-matcher запроса против `tz_summary` (правило
  #17). `MATCHER_VERSION`, `match_and_save()` — паттерн `classify/llm.py`
  (strict tool, ретраи 429, ошибки не наружу).
- `classify/usage.py` — учёт расхода токенов. `usage_from_response(resp,
  model)` достаёт `prompt/completion/total_tokens` из ответа Cerebras;
  `record_call(session, kind, usage, ...)` пишет строку `LlmCall` (defensive,
  не роняет пайплайн — правило #7). Зовётся из трёх точек: `analyze_and_save`
  (`kind="analyze"`), `match_and_save` (`kind="match"`), `web.app.lot_chat`
  (`kind="chat"`). Учитываются ТОЛЬКО реальные вызовы — скипы по
  идемпотентности, копии по simhash и rule-based строк не создают. Отчёт —
  страница `/expenses` (admin), агрегация по дням/неделям/месяцам в Python
  (dialect-независимо), стоимость — оценка по `config.LLM_PRICE_*` (на
  free-tier Cerebras фактически $0).
- `queue/matching.py` — `match_actor` (очередь `goszakup_matching`, флаг
  `notify`) + `enqueue_matches_for_lot` (fan-out с pre-filter по scope,
  `notify=True`) и `enqueue_matches_for_query` (backfill, `notify=False`).
- `queue/notify.py` — `notify_actor` (очередь `goszakup_notify`): шлёт
  Telegram по положительному матчу, дедуп по `UserLotMatch.notified_at`
  (правило #18).
- `notify/telegram.py` — defensive-обёртка над Bot API `sendMessage`
  (`send_message(chat_id, text) -> (ok, error)`); `notify/render.py` —
  сборка HTML-текста уведомления по лоту/матчу (с экранированием).
- `scope.py` — `scope_conditions(user)` / `lot_in_scope(lot, user)` — единый
  источник правды для read-time scope (правило #15); используется и web,
  и matcher-fan-out'ом.
- `vault/` — KeyVault автоподачи (правило #19). `crypto.py` — AES-256-GCM
  (мастер-ключ из `GZ_VAULT_MASTER_KEY`, ленивый — модуль грузится и без него).
  `credentials.py` — `create_credential`/`decrypt_credential` поверх crypto.
- `autosubmit/` — provider-agnostic ядро автоподачи (правило #19). `timing.py`
  (NTP-упреждение + busy-wait к `open_at`), `fire.py` (httpx-«выстрел» финального
  POST `ajax_public_application`), `rpc.py` (контракт Linux↔Windows agent),
  `agent_client.py` (HTTP к agent'у), `scheduler.py` (`dispatch_due_submissions`
  к `open_at` → ARMED; `apply_result` ← `RunResult`).
- `queue/autosubmit.py` — `autosubmit_dispatch_actor` (очередь
  `goszakup_autosubmit`): таймер-диспетчер задач submit-agent'у.
- `agent/` (корень репо, НЕ часть пакета goszakup) — Windows submit-agent
  (правило #19, Phase 2). Отдельный деплой на Windows-узле. `server.py` (HTTP
  `POST /run`/`GET /health`), `runner.py` (оркестрация), `wizard.py` (Playwright,
  `TODO(recon)`), `tumar.py` (pywinauto окно цены, `TODO(recon)`), `report.py`
  (отчёт на Linux), `protocol.py`/`timing.py` (зеркало `autosubmit/`), `RECON.md`
  + `recon_dump.py` (снять данные с живого конкурса). Зависит только от
  httpx+playwright+pywinauto.
- `db/models.py` — 14 таблиц. `Organization` — общая для customer/organizer/supplier.
  `User` — учётка для входа (bcrypt-пароль, `is_admin`, scope-поля
  `regions`/`it_categories`/`min_amount`); данные с лотами не связаны FK
  (изоляция read-time, правило #15).
  `LotAnalysis` — 1:1 к `Lot` через FK + UniqueConstraint.
  `UserQuery`/`UserLotMatch` — семантический подбор (правило #17); матчи
  уникальны по `(user_query_id, lot_id)`, удаление запроса каскадит матчи.
  `ClientCredential`/`Submission` — автоподача (правило #19): зашифрованные
  секреты клиента и запланированная/исполненная подача со статус-машиной
  `SUBMISSION_STATUSES` (цена в `bid_enc` шифрованная).
- `db/engine.py` — `init_db()` делает `create_all` + узкий `_ensure_columns()`
  для редких ALTER TABLE на legacy-БД. Это safety net; **канонические
  миграции** живут в `migrations/versions/` (Alembic). Engine создан с
  `connect_args={"timeout": 30}`; event listener `_sqlite_pragmas` на каждом
  connect выставляет `PRAGMA journal_mode=WAL` и `PRAGMA synchronous=NORMAL`.
  WAL включается на уровне файла БД и сохраняется между запусками.
- `migrations/env.py` — Alembic-env берёт URL из `goszakup.config.DB_URL`
  (а не из `alembic.ini`), включён `render_as_batch=True` для SQLite-ALTER'ов.
  `GZ_DATABASE_URL` env-переменная перекрывает дефолт (используется тестами
  и в Phase 2 для Postgres).
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

# Логи прогонов
# На проде: sudo journalctl -u goszakup-daily --since today
# На macOS (launchd): ls -lt data/logs/ | head
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
