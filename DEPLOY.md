# DEPLOY.md — развёртывание и перенос Goszakup Tracker на новый сервер

Полный рецепт: поднять проект на чистом Ubuntu 24.04 (22.04 — так же) и
перевезти на него данные с текущего прода `gost.salemsoft.kz`.

Документ заменяет прежнюю редакцию (эпоха SQLite + Basic Auth). Актуальная
раскладка сегодня: **Postgres 15, Dramatiq+Redis, форма-логин, systemd,
nginx, SOCKS-туннель в KZ**.

Обозначения: `СТАРЫЙ` — сервер, с которого уезжаем, `НОВЫЙ` — на который
переезжаем. Пути даны под конвенцию прода (`/home/rus/projects/gos_tracker`,
юзер `rus`); если у вас другой путь/юзер — см. §7.4, там про абсолютные пути
в БД, это не косметика.

---

## 1. Что именно переносится

| Компонент | Что это | Объём на 2026-08-12 |
|---|---|---|
| Код | git-репозиторий `github.com/Rus9136/gos_tracker` | ~10 МБ |
| Postgres `goszakup_prod` | лоты, планы, анализы, матчи, пользователи | **453 МБ** (33.7k лотов, 154k пунктов плана, 26k объявлений) |
| `data/docs/` | скачанные ТЗ (PDF/DOCX) | **2.7 ГБ**, 24 885 файлов |
| `data/vendor/` | дистрибутивы Tumar/CryptoSocket (правило #19) | 75 МБ |
| `.env` | все секреты | 2 КБ |
| `~/.ssh/id_kz_proxy` | ключ SOCKS-туннеля в KZ | 432 Б |
| systemd-юниты | web, worker, 3 таймера (+2 опциональных) | шаблоны в `scripts/systemd/` |
| nginx + LE-сертификат | `gost.salemsoft.kz` | сертификат выпускается заново |

Не переносим: `.venv/` (пересобрать), `data/goszakup.sqlite*` (legacy-фолбэк
до cutover 2026-05-20, данные там протухли), `data/logs/`, `data/backups/`
(SQLite-эпоха), `*.har` (содержат пароли портала и сертификаты — их вообще
надо удалять, а не возить).

---

## 2. Требования к новому серверу

### 2.1 Железо

Текущий прод делит 8 vCPU / 23 ГБ RAM с десятком других проектов. Самому
трекеру достаточно:

- **2–4 vCPU** (worker `-p 2 -t 4`; на goszakup-фазе всё равно упирается
  в rate-limit, не в CPU);
- **4–8 ГБ RAM** (dramatiq-worker в пике ~1.1 ГБ RSS: pdfplumber на больших
  PDF; postgres ещё 1–2 ГБ);
- **60+ ГБ диска** — 3 ГБ данных сейчас, но `data/docs` растёт линейно с
  числом отслеживаемых лотов, а после SaaS-пивота (правило #24) хранятся
  все лоты рынка. Заложите запас; чистка — `jobs/retention.py`.

### 2.2 Софт

| Пакет | Версия | Зачем |
|---|---|---|
| Python | **3.11+** (на проде 3.12, CI тоже 3.12) | `int \| None`-синтаксис; на 3.9 не запустится |
| PostgreSQL | 15 или 16 | основная БД (CI гоняет на 16, прод на 15) |
| Redis | 7 | очередь Dramatiq + cross-process rate-limit |
| nginx | 1.18+ | TLS-терминация и reverse proxy |
| certbot | любой | Let's Encrypt |
| git, curl, rsync | — | деплой и перенос |
| Docker | опционально | если Redis поднимаете контейнером (как на проде) |

Системные заголовки (`libxml2-dev` и пр.) **не нужны**: lxml, psycopg,
pypdfium2 ставятся бинарными колёсами. `build-essential` держите только как
страховку на экзотической архитектуре.

### 2.3 Сеть — самое важное

- **Исходящий доступ к `goszakup.gov.kz` и `v3bl.goszakup.gov.kz`.**
  С 2026-07-16 goszakup дропает коннекты с зарубежных IP (см. память
  `goszakup-blocks-foreign-ip`). Если новый сервер **в Казахстане** — туннель
  не нужен, просто не задавайте `GZ_PROXY_URL`. Если **вне KZ** — обязателен
  SOCKS-туннель (§9).
- **`ows.goszakup.gov.kz`** — доступен с зарубежного IP напрямую, туннель ему
  не нужен (правило #21).
- **`api.cerebras.ai`** — LLM-классификация.
- **`api.telegram.org`** — уведомления; плюс входящий HTTPS на вебхук.
- Порты наружу: только **22/80/443**. UI (8765) и Redis (6380) — только
  на `127.0.0.1`.

### 2.4 Часовой пояс

Текущий прод живёт в `Europe/Berlin`, и `goszakup-daily.timer` в 06:00 CEST
= 09:00 по Алматы. Таймеры считают время сервера, а не Алматы. Решите
сознательно:

```bash
timedatectl set-timezone Asia/Almaty   # тогда daily пойдёт в 06:00 по Алматы
```

Даты из API OWS парсятся как UTC+5 и хранятся в UTC (правило #21) — от
таймзоны сервера это не зависит, страдает только расписание таймеров.

---

## 3. Установка системных пакетов (НОВЫЙ)

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip git rsync curl \
    postgresql-15 postgresql-client-15 nginx certbot python3-certbot-nginx
```

Если в дистрибутиве нет `python3.12` — берите `python3.11` (минимум по
`requires-python`) или PGDG/deadsnakes.

Redis — либо системный, либо docker-контейнер (на проде второе, чтобы не
конфликтовать с чужим `shared-redis` на 6379):

```bash
# Вариант A (как на проде): выделенный контейнер на 6380
sudo apt install -y docker.io
sudo docker run -d --name goszakup-redis --restart unless-stopped \
    -p 127.0.0.1:6380:6379 \
    redis:7-alpine redis-server --save "" --appendonly no

# Вариант B (чистая машина): системный redis на 6379
sudo apt install -y redis-server
sudo systemctl enable --now redis-server
```

AOF/RDB выключены намеренно: очередь и rate-limit персистентности не
требуют. Плата за это — потеря pending-счётчика прогонов при рестарте, её
компенсирует БД-heartbeat (правило #14).

---

## 4. Пользователь и каталог (НОВЫЙ)

Прод-конвенция — обычный юзер `rus` и `/home/rus/projects/`:

```bash
sudo adduser --disabled-password --gecos "" rus   # если пользователя ещё нет
sudo -u rus mkdir -p /home/rus/projects
```

Если решите ставить в `/opt/goszakup` под системного юзера — придётся
переписать пути **во всех** юнитах `scripts/systemd/*` и обязательно
выполнить §7.4 (абсолютные пути документов в БД).

---

## 5. Postgres (НОВЫЙ)

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE goszakup LOGIN PASSWORD 'ПОСТАВЬТЕ_СВОЙ_ПАРОЛЬ';
CREATE DATABASE goszakup_prod OWNER goszakup ENCODING 'UTF8';
SQL
```

Кластер слушает `127.0.0.1:5432` по умолчанию — этого достаточно, наружу
Postgres выставлять не надо. Проверка:

```bash
PGPASSWORD='...' psql -h 127.0.0.1 -U goszakup -d goszakup_prod -c 'SELECT 1;'
```

> Если на новой машине 5432 уже занят чужим кластером — поднимите свой на
> другом порту и пропишите его в `GZ_DATABASE_URL`; больше нигде порт не
> зашит.

---

## 6. Код и venv (НОВЫЙ)

```bash
sudo -u rus git clone https://github.com/Rus9136/gos_tracker.git \
    /home/rus/projects/gos_tracker
cd /home/rus/projects/gos_tracker

sudo -u rus python3.12 -m venv .venv
sudo -u rus ./.venv/bin/pip install --upgrade pip
sudo -u rus ./.venv/bin/pip install -e .
sudo -u rus ./.venv/bin/python -c "import sys; print(sys.version)"   # 3.12.x
```

`pip install -e .` тянет всё из `pyproject.toml`: fastapi/uvicorn,
sqlalchemy+alembic, `psycopg[binary]`, `dramatiq[redis]`, requests[socks],
pdfplumber/python-docx, cerebras-cloud-sdk, bcrypt, cryptography, httpx,
sentry-sdk.

---

## 7. Перенос данных

### 7.1 `.env`

Копируем со СТАРОГО и правим (полный разбор переменных — §8):

```bash
# со СТАРОГО
scp /home/rus/projects/gos_tracker/.env НОВЫЙ:/home/rus/projects/gos_tracker/.env
# на НОВОМ
chmod 600 /home/rus/projects/gos_tracker/.env
```

Что **обязательно** поменять после копирования: пароль в `GZ_DATABASE_URL`
(новый кластер — новый пароль), `GZ_PROXY_URL` (убрать, если сервер в KZ),
`GZ_PUBLIC_BASE_URL` (если меняется домен).

`GZ_SECRET_KEY` наоборот — **сохраните прежний**, иначе у всех пользователей
слетят сессии (не смертельно, просто перелогин).

### 7.2 База: дамп и восстановление

На СТАРОМ (лучше после остановки писателей — см. §12):

```bash
cd /home/rus/projects/gos_tracker
PGPASSWORD=$(grep '^GZ_DATABASE_URL=' .env | sed -E 's|.*//goszakup:([^@]+)@.*|\1|') \
  pg_dump -h 127.0.0.1 -p 5432 -U goszakup -d goszakup_prod \
  --format=custom --no-owner --no-privileges \
  -f /tmp/goszakup_prod_$(date +%F).dump

ls -lh /tmp/goszakup_prod_*.dump    # ~46 МБ на 2026-08-12 (custom-формат сжат)
scp /tmp/goszakup_prod_*.dump НОВЫЙ:/tmp/
```

На НОВОМ:

```bash
PGPASSWORD='...' pg_restore -h 127.0.0.1 -p 5432 -U goszakup -d goszakup_prod \
    --no-owner --no-privileges --jobs 4 /tmp/goszakup_prod_2026-08-12.dump
```

`--format=custom` тащит и sequences в актуальном состоянии — руками их
бампать не нужно (в отличие от миграции SQLite→PG, там это делал
`scripts/migrate_sqlite_to_pg.py::_bump_sequences`).

Проверка после restore:

```bash
PGPASSWORD='...' psql -h 127.0.0.1 -U goszakup -d goszakup_prod \
  -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 10;" \
  -c "SELECT count(*) FROM users;" \
  -c "SELECT version_num FROM alembic_version;"
```

`alembic_version` должен совпасть с `head` в репозитории (на 2026-08-12 —
`bb09e3705f64`). Тогда `alembic upgrade head` на новом коде отработает как
обычный инкремент; `alembic stamp` **не делать** — версия уже приехала
внутри дампа.

### 7.3 Файлы документов

```bash
# со СТАРОГО (2.7 ГБ, 25k файлов — идёт десятки минут)
rsync -avz --info=progress2 \
  /home/rus/projects/gos_tracker/data/docs/ \
  НОВЫЙ:/home/rus/projects/gos_tracker/data/docs/

# вендорские дистрибутивы крипто (нужны только для автоподачи, правило #19)
rsync -avz /home/rus/projects/gos_tracker/data/vendor/ \
  НОВЫЙ:/home/rus/projects/gos_tracker/data/vendor/
```

rsync идемпотентен — можно гонять заранее и повторить дельту в момент
cutover.

Потерять `data/docs` не фатально (LLM-анализ уже в БД, файл при нужде
перекачается), но перекачка 25k файлов упрётся в Crawl-delay и займёт
недели. Везите.

### 7.4 ⚠️ Абсолютные пути в `documents.local_path`

**Это главная грабля переезда.** В БД лежат абсолютные пути:

```
/home/rus/projects/gos_tracker/data/docs/17193628/konkurs_application_2_2025.docx
```

Таких строк 25 456. Если каталог проекта на новом сервере тот же — ничего
делать не надо. Если путь изменился (например `/opt/goszakup/app`), после
restore выполните:

```sql
UPDATE documents
   SET local_path = replace(local_path,
                            '/home/rus/projects/gos_tracker/data/docs',
                            '/opt/goszakup/app/data/docs')
 WHERE local_path LIKE '/home/rus/projects/gos_tracker/data/docs%';
```

Без этого: карточка лота отдаёт «файл не найден», чат по ТЗ и
переанализ теряют текст спецификации, а `analyze_and_save` тихо уходит в
`analysis_confidence='low'` — по правилу #7 никаких ошибок вы не увидите.

Проверка, что не осталось битых ссылок:

```bash
PGPASSWORD='...' psql -h 127.0.0.1 -U goszakup -d goszakup_prod -tAc \
  "SELECT local_path FROM documents WHERE local_path IS NOT NULL LIMIT 5;"
# затем убедиться, что файлы существуют:
PGPASSWORD='...' psql -h 127.0.0.1 -U goszakup -d goszakup_prod -tAc \
  "SELECT local_path FROM documents WHERE local_path IS NOT NULL" \
  | while read -r p; do [ -f "$p" ] || echo "MISSING: $p"; done | head
```

---

## 8. Переменные окружения (`.env`)

Файл читается `python-dotenv` из `config.py` при импорте — то есть всеми
точками входа (web, worker, CLI) автоматически. `EnvironmentFile=` в юнитах
не нужен и на проде не используется.

### 8.1 Обязательные

| Переменная | Значение | Что будет без неё |
|---|---|---|
| `GZ_DATABASE_URL` | `postgresql+psycopg://goszakup:PASS@127.0.0.1:5432/goszakup_prod` | молча свалится на SQLite `data/goszakup.sqlite` — пустая база, «пропали все данные» |
| `GZ_SECRET_KEY` | `openssl rand -hex 32` | web и worker **не стартуют** (`require_safe_secret_key`), кроме `GZ_NO_AUTH=1` |
| `CEREBRAS_API_KEY` | `csk-...` | LLM-анализ и матчинг молча выключены (правило #7); сторож из правила #20 поднимет тревогу |
| `GZ_REDIS_URL` | `redis://127.0.0.1:6380/0` (или `:6379`) | очередь не работает, `daily` ничего не делает |

### 8.2 Нужные конкретно этому проду

| Переменная | Значение на проде | Комментарий |
|---|---|---|
| `GZ_OWS_TOKEN` | Bearer от ЦЭФ | без него всё уедет на HTML-скрейпинг: медленнее в 30-50× (правило #21) |
| `GZ_OWS_TOKEN_EXPIRES` | `2027-07-24` | health-check предупредит за 14 дней; истёкший токен маскируется под 404 |
| `GZ_API_DELAY` | `1.0` | пауза между запросами к OWS |
| `GZ_PROXY_URL` | `socks5h://127.0.0.1:1080` | **только если сервер вне KZ** (§9). Сервер в KZ — переменную убрать |
| `GZ_TELEGRAM_BOT_TOKEN` | токен @BotFather | уведомления о матчах (правило #18) |
| `GZ_TELEGRAM_WEBHOOK_SECRET` | `openssl rand -hex 32` | кнопка «Подробнее»; после переезда вебхук перерегистрировать (§11.3) |
| `GZ_PUBLIC_BASE_URL` | `https://gost.salemsoft.kz` | ссылки в уведомлениях; **сменить при смене домена** |
| `GZ_USER` / `GZ_PASSWORD` | сид первого админа | срабатывает только при пустой таблице `users`; после restore база не пуста — эффекта нет |

### 8.3 Опциональные

`GZ_LLM_MODEL` (дефолт `gpt-oss-120b`), `GZ_DATA_DIR` (обязателен только в
Docker), `GZ_HEALTH_MATCH_STALE_HOURS` / `GZ_HEALTH_ALERT_COOLDOWN`,
`SENTRY_DSN`/`SENTRY_ENVIRONMENT`/`SENTRY_RELEASE`,
`GZ_LLM_PRICE_INPUT`/`GZ_LLM_PRICE_OUTPUT` (оценка на `/expenses`),
`GZ_OWS_USE_PROXY` (закладка на случай геоблока OWS).

Автоподача (правило #19, включать только вместе с submit-agent):
`GZ_VAULT_MASTER_KEY` (base64 32 байт), `GZ_AUTOSUBMIT_AGENT_URL`,
`GZ_AUTOSUBMIT_AGENT_TOKEN`, `GZ_AUTOSUBMIT_INGEST_TOKEN`,
`GZ_AUTOSUBMIT_WARMUP_LEAD`, `GZ_AUTOSUBMIT_AGENT_ALLOW_HTTP`.

**`GZ_NO_AUTH=1` на проде не ставить никогда** — он выключает логин целиком
и пускает синтетическим админом.

---

## 9. SOCKS-туннель в KZ (только для сервера вне Казахстана)

goszakup.gov.kz дропает TCP с зарубежных IP, поэтому весь скрейперный
трафик идёт через SSH-SOCKS на KZ-хост. OWS-API ходит мимо туннеля.

1. Перевезти ключ и разрешить новый сервер на KZ-хосте:

```bash
# со СТАРОГО
scp ~/.ssh/id_kz_proxy НОВЫЙ:/home/rus/.ssh/id_kz_proxy
# на НОВОМ
chmod 600 /home/rus/.ssh/id_kz_proxy
ssh -i /home/rus/.ssh/id_kz_proxy root@89.207.254.238 'echo tunnel-ok'
```

Ключ авторизуется на стороне KZ-хоста, а не по IP, — если в его
`sshd_config` нет ограничения `from=`, ничего дополнительно не нужно. Если
есть — добавьте IP нового сервера в `authorized_keys`.

2. Юнит `/etc/systemd/system/goszakup-tunnel.service`:

```ini
[Unit]
Description=Goszakup Tunnel — SOCKS5 через KZ-сервер (обход геоблока)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rus
Group=rus
ExecStart=/usr/bin/ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 -o IdentitiesOnly=yes -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new -i /home/rus/.ssh/id_kz_proxy \
    -D 127.0.0.1:1080 root@89.207.254.238
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now goszakup-tunnel
curl -sS --socks5-hostname 127.0.0.1:1080 -o /dev/null -w '%{http_code}\n' \
     https://goszakup.gov.kz/ru/search/lots     # ждём 200
```

При `ConnectTimeout` в логах воркера первым делом проверять
`systemctl status goszakup-tunnel`.

---

## 10. systemd-юниты

Шаблоны лежат в `scripts/systemd/` уже с правильными путями:

```bash
cd /home/rus/projects/gos_tracker
sudo cp scripts/systemd/goszakup-*.service scripts/systemd/goszakup-*.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
```

Плюс `goszakup-web.service` (в шаблонах его нет — создайте вручную):

```ini
[Unit]
Description=Goszakup Tracker — FastAPI UI
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=rus
Group=rus
WorkingDirectory=/home/rus/projects/gos_tracker
ExecStart=/home/rus/projects/gos_tracker/.venv/bin/python -m uvicorn \
    goszakup.web.app:app --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
ProtectSystem=strict
PrivateTmp=true
ReadWritePaths=/home/rus/projects/gos_tracker/data

[Install]
WantedBy=multi-user.target
```

Включаем:

```bash
sudo systemctl enable --now goszakup-web.service
sudo systemctl enable --now goszakup-worker.service
sudo systemctl enable --now goszakup-daily.timer      # 06:00, весь конвейер
sudo systemctl enable --now goszakup-expire.timer     # ежечасно :05
sudo systemctl enable --now goszakup-health.timer     # ежечасно :35
# автоподача — только когда готов submit-agent (правило #19):
# sudo systemctl enable --now goszakup-autosubmit.timer

systemctl list-timers 'goszakup-*'
```

`goszakup-backup.*` — артефакт SQLite-эпохи, на Postgres не нужен;
вместо него настройте `pg_dump` по крону (§13).

### Подводные камни юнитов

- **`ProtectHome=true` в worker/web не ставить** — venv лежит в `/home/rus`,
  иначе systemd отдаст `203/EXEC`.
- **Очереди перечислены явно** в `--queues` воркера:
  `goszakup_daily goszakup_listing goszakup_detail goszakup_llm
  goszakup_matching goszakup_notify goszakup_autosubmit`. Добавили актор с
  новой очередью — допишите сюда, иначе задачи молча копятся в Redis.
- `goszakup-worker.service` содержит `Requires=docker.service` — если Redis
  у вас системный (вариант B из §3), замените на `redis-server.service`.
- В live-юните `goszakup-daily.service` на старом сервере остались мёртвые
  `ConditionPathExists`/`ExecStartPre`/`ExecStopPost` вокруг `data/.daily.lock`
  (артефакт синхронного daily до Phase 3). Шаблон в репозитории уже чистый —
  на новом сервере ставьте шаблон, lock-файл не нужен.

---

## 11. nginx, TLS, домен

### 11.1 DNS

A-запись домена → IP нового сервера. TTL заранее снизить до 300с, чтобы
переключение прошло быстро.

### 11.2 nginx

`/etc/nginx/sites-available/gost.salemsoft.kz.conf` — рабочий конфиг прода:

```nginx
server {
    listen 80;
    server_name gost.salemsoft.kz;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$server_name$request_uri; }
}

server {
    listen 443 ssl http2;
    server_name gost.salemsoft.kz;

    ssl_certificate     /etc/letsencrypt/live/gost.salemsoft.kz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gost.salemsoft.kz/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    access_log /var/log/nginx/gost.salemsoft_access.log;
    error_log  /var/log/nginx/gost.salemsoft_error.log;

    client_max_body_size 16M;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # ручные «загрузить документы» / «переанализировать» идут десятки секунд
        proxy_connect_timeout 30s;
        proxy_send_timeout   600s;
        proxy_read_timeout   600s;
    }

    gzip on;
    gzip_vary on;
    gzip_types text/plain text/css text/xml text/javascript
               application/javascript application/json application/xml;
    gzip_min_length 1000;
    gzip_comp_level 6;
}
```

`proxy_read_timeout 600s` — не украшение: ручные действия на `/lot/{id}`
идут долго, с дефолтными 60с пользователь ловит 504.

```bash
sudo mkdir -p /var/www/certbot
sudo ln -s /etc/nginx/sites-available/gost.salemsoft.kz.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 11.3 Сертификат

Выпускать **после** переключения DNS (webroot требует, чтобы домен уже
резолвился на новый сервер):

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d gost.salemsoft.kz
sudo systemctl reload nginx
systemctl list-timers certbot.timer      # автопродление ставит сам пакет
```

Альтернатива без простоя: скопировать `/etc/letsencrypt/` со старого
сервера целиком — сертификат валиден до истечения, а `certbot.timer`
продлит его уже на новом месте.

### 11.4 Telegram-вебхук

После смены сервера/домена **обязательно** перерегистрировать — иначе
кнопка «Подробнее» будет стучаться на старый хост:

```bash
cd /home/rus/projects/gos_tracker
./.venv/bin/python -m goszakup.cli telegram-set-webhook
```

Бот один на сервис: пока вебхук указывает на новый сервер, старый его
больше не получает. Оба сервера одновременно обслуживать вебхук не могут —
это ещё один аргумент не держать их параллельно (§12).

---

## 12. Cutover: порядок с минимальным простоем

Простой ≈ 10–20 минут (упирается в дельта-дамп БД), при заранее
прогнанном rsync документов.

**За сутки:** снизить TTL DNS до 300с; поднять на НОВОМ всё по §3–§10, кроме
включения таймеров; прогнать первый (долгий) `rsync data/docs`.

**Cutover:**

```bash
# 1. СТАРЫЙ — остановить писателей (UI можно оставить в read-only до конца)
sudo systemctl stop goszakup-daily.timer goszakup-expire.timer \
                    goszakup-health.timer goszakup-autosubmit.timer
sudo systemctl stop goszakup-worker.service
pgrep -af 'goszakup.cli'      # убедиться, что ручных прогонов нет
sudo systemctl stop goszakup-web.service

# 2. СТАРЫЙ — финальный дамп + дельта документов
pg_dump ... -Fc -f /tmp/final.dump          # команда из §7.2
rsync -avz --info=progress2 data/docs/ НОВЫЙ:.../data/docs/

# 3. НОВЫЙ — залить дамп в ПУСТУЮ базу
dropdb ... && createdb ...                  # если уже заливали тестовый дамп
pg_restore ... /tmp/final.dump              # команда из §7.2
# при смене пути проекта — UPDATE documents.local_path (§7.4)
./.venv/bin/alembic upgrade head            # no-op, если код тот же

# 4. НОВЫЙ — поднять сервисы и прогнать smoke (§13)
sudo systemctl start goszakup-web goszakup-worker
sudo systemctl start goszakup-daily.timer goszakup-expire.timer goszakup-health.timer

# 5. Переключить DNS, дождаться резолва, выпустить сертификат (§11.3)
# 6. Перерегистрировать Telegram-вебхук (§11.4)
```

**Держать оба сервера работающими нельзя**: две независимые сессии к
goszakup суммарно нарушат Crawl-delay, два воркера будут дублировать
задачи, а вебхук всё равно достанется одному.

**Первую неделю** старый сервер не сносить — это план отката (§14).

---

## 13. Проверка после переезда

```bash
cd /home/rus/projects/gos_tracker

# 1. Конфигурация читается, БД та самая
./.venv/bin/python -m goszakup.cli stats
./.venv/bin/python -m goszakup.cli presets      # должно быть 20
./.venv/bin/python -m goszakup.cli list-users   # пользователи приехали

# 2. Схема на месте
./.venv/bin/alembic current                     # совпадает с head

# 3. Сервисы живы
systemctl status goszakup-web goszakup-worker --no-pager
systemctl list-timers 'goszakup-*'
sudo journalctl -u goszakup-worker -n 50        # dramatiq подписался на 7 очередей

# 4. Redis доступен
redis-cli -p 6380 ping                          # PONG

# 5. Сеть до goszakup (через туннель, если он нужен)
curl -sS --socks5-hostname 127.0.0.1:1080 -o /dev/null -w '%{http_code}\n' \
     https://goszakup.gov.kz/ru/search/lots

# 6. LLM-контур и OWS-токен — одной командой
./.venv/bin/python -m goszakup.cli health-check   # exit 0 = всё живо

# 7. UI
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/login   # 200
```

Ручная проверка в браузере (то, что автоматика не покрывает):

1. Вход по форме `/login` старым паролем.
2. `/actual` — лоты видны, колонка «Короткое ТЗ» заполнена.
3. Открыть карточку лота → **скачать документ** (проверяет §7.4).
4. Задать вопрос в чате по ТЗ (LLM + текст файла).
5. `/queries` → «Подобрать сейчас» на существующем запросе → матчи появились.
6. Дождаться первого ночного `daily`, утром: `journalctl -u goszakup-daily
   --since today` и `/runs` — прогон закрылся (`finished_at` не NULL).

Резервные копии на новом месте (замена SQLite-бэкапам):

```bash
# /etc/cron.d/goszakup-backup или systemd-timer
0 3 * * * rus PGPASSWORD=... pg_dump -h 127.0.0.1 -U goszakup -d goszakup_prod \
    -Fc -f /home/rus/backups/goszakup-$(date +\%F).dump && \
    find /home/rus/backups -name 'goszakup-*.dump' -mtime +14 -delete
```

---

## 14. Откат

Пока старый сервер не тронут, откат = вернуть DNS обратно и поднять на нём
сервисы:

```bash
# СТАРЫЙ
sudo systemctl start goszakup-web goszakup-worker
sudo systemctl start goszakup-daily.timer goszakup-expire.timer goszakup-health.timer
# вернуть DNS, перерегистрировать вебхук со старого хоста
```

Данные, набранные новым сервером за время работы, при откате теряются —
поэтому решение об откате принимать в первые часы, а не через неделю.

---

## 15. Альтернатива: Docker Compose

`docker-compose.yml` поднимает postgres + redis + web + worker одной
командой. Годится для staging и быстрой проверки, на проде не используется.

```bash
cp env.example .env      # заполнить как минимум CEREBRAS_API_KEY, GZ_SECRET_KEY
docker compose up -d
docker compose exec web alembic upgrade head
docker compose exec web python -m goszakup.cli seed-presets
# UI: http://127.0.0.1:8766
```

Нюансы: внутри контейнера обязателен `GZ_DATA_DIR=/app/data` (иначе config
попытается писать в site-packages); порты сдвинуты (8766/5433), чтобы не
конфликтовать с системными сервисами; шаблоны и статика попадают в wheel
через `[tool.setuptools.package-data]` — без этого uvicorn падает на
`mount("/static")`.

---

## 16. Обновление кода (после переезда — штатный релиз)

```bash
cd /home/rus/projects/gos_tracker
git pull --ff-only
./.venv/bin/pip install -e .                    # только если менялся pyproject.toml
./.venv/bin/alembic upgrade head                # если были миграции
sudo systemctl restart goszakup-web.service
sudo systemctl restart goszakup-worker.service
```

Таймеры перезапускать не нужно — следующий oneshot подхватит свежий код.

⚠️ **Воркер держит код момента старта.** Если в релизе изменились акторы,
`goszakup-worker` обязателен к рестарту, иначе он продолжит исполнять старую
версию (и вы получите «фантомные» задачи без следа в исходниках).

---

## 17. Траблшутинг

| Симптом | Причина / что смотреть |
|---|---|
| web/worker не стартуют, в логе `GZ_SECRET_KEY не задан` | `require_safe_secret_key` — заполнить `GZ_SECRET_KEY` в `.env` |
| UI пустой, «пропали все данные» | не подхватился `GZ_DATABASE_URL` → ушли на SQLite-фолбэк. `journalctl -u goszakup-web \| grep -i sqlite` |
| `203/EXEC` у worker/web | в юните `ProtectHome=true`, а venv в `/home/rus` — убрать |
| 502 от nginx | uvicorn упал: `systemctl status goszakup-web`, `journalctl -u goszakup-web -n 200` |
| 504 на «Загрузить документы» | в nginx не выставлен `proxy_read_timeout 600s` |
| Документ не открывается с карточки лота | `documents.local_path` указывает на старый путь — §7.4 |
| Задачи копятся, ничего не исполняется | очередь не перечислена в `--queues` воркера; `redis-cli -p 6380 keys 'dramatiq:*'` |
| «идёт прогон #N» висит вечно | потерян Redis-счётчик; закроет reaper по heartbeat за 15 мин (правило #14) |
| `ConnectTimeout` к goszakup | туннель лёг: `systemctl status goszakup-tunnel`, проверить curl через socks |
| Пусто в анализе, ошибок нет | LLM молчит по правилу #7 — `cli health-check`, проверить квоту Cerebras (402 payment_required) |
| API OWS отдаёт 404 «Invalid Route» | истёк/невалиден `GZ_OWS_TOKEN` — это не «нет роута», это авторизация |
| Telegram-кнопка «Подробнее» не отвечает | вебхук указывает на старый сервер — `cli telegram-set-webhook` |
| `alembic upgrade head` падает «таблица уже существует» | базу залили не дампом, а `create_all` — восстановить из дампа (в нём есть `alembic_version`) |

Полезное:

```bash
sudo journalctl -u goszakup-web -f
sudo journalctl -u goszakup-worker -f
sudo journalctl -u goszakup-daily --since today
sudo tail -f /var/log/nginx/gost.salemsoft_error.log
systemctl list-units --failed | grep goszakup      # health-check падает в failed при проблеме
```

---

## 18. Чеклист переезда

- [ ] Сервер: python 3.11+, PG 15/16, Redis 7, nginx, certbot, git, rsync
- [ ] Часовой пояс выбран осознанно (§2.4)
- [ ] Postgres: роль `goszakup` + база `goszakup_prod`
- [ ] Redis слушает `127.0.0.1` (6380 в контейнере или 6379 системный)
- [ ] Репозиторий склонирован, venv собран, `pip install -e .`
- [ ] `.env` перевезён, `chmod 600`, пароль БД/прокси/домен поправлены
- [ ] `GZ_SECRET_KEY` сохранён прежний
- [ ] Дамп БД восстановлен, `alembic_version` = head
- [ ] `data/docs` (2.7 ГБ) и `data/vendor` перевезены
- [ ] `documents.local_path` соответствует новому пути (§7.4)
- [ ] Туннель поднят и проверен curl'ом (если сервер вне KZ)
- [ ] systemd: web, worker, daily/expire/health-таймеры включены
- [ ] Очереди в `--queues` воркера совпадают со списком акторов
- [ ] nginx + сертификат, `proxy_read_timeout 600s`
- [ ] DNS переключён
- [ ] Telegram-вебхук перерегистрирован
- [ ] `cli health-check` → exit 0
- [ ] Ручной smoke: вход, `/actual`, скачивание документа, чат, матчинг
- [ ] Бэкап БД по крону настроен
- [ ] Старый сервер остановлен, но не удалён (неделя на откат)
