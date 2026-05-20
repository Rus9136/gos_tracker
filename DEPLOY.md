# DEPLOY.md — Goszakup Tracker на Linux-сервер

Инструкция под чистый Ubuntu 22.04/24.04 (Debian 12 — аналогично). Считаем,
что есть root-доступ и доменное имя/IP сервера. Все пути ниже —
`/opt/goszakup`, пользователь — `goszakup`. Замените при необходимости.

> **Фактическая раскладка прода — в `CLAUDE.md`** (раздел «Продакшн:
> gost.salemsoft.kz»): `/home/rus/projects/gos_tracker`, юзер `rus`,
> системный Postgres-15 рядом с другими `*.salemsoft.kz` проектами.
> Cutover с SQLite → Postgres выполнен 2026-05-20. Этот документ —
> общий рецепт для чистого сервера.

---

## 0. Предварительно

На сервере нужны:

- Python **3.11+** (обязательно — в коде `int | None`-синтаксис).
- Git.
- Системные либы для pdfplumber/python-docx и сборки колёс:
  `build-essential`, `libxml2-dev`, `libxslt1-dev`, `zlib1g-dev`,
  `libjpeg-dev`, `poppler-utils`.
- Доступ в интернет до `goszakup.gov.kz`, `v3bl.goszakup.gov.kz` и
  `api.cerebras.ai` (исходящий HTTPS).

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git \
    build-essential libxml2-dev libxslt1-dev zlib1g-dev libjpeg-dev \
    poppler-utils
```

Если в репозитории дистрибутива нет `python3.11`, поставьте через
`deadsnakes` PPA или скомпилируйте отдельно.

---

## 1. Системный пользователь и каталог

Отдельный пользователь без shell-логина — чтобы сервис не имел лишних прав:

```bash
sudo useradd --system --create-home --home-dir /opt/goszakup \
    --shell /usr/sbin/nologin goszakup
sudo mkdir -p /opt/goszakup
sudo chown -R goszakup:goszakup /opt/goszakup
```

---

## 2. Клонирование репозитория

```bash
sudo -u goszakup git clone https://github.com/Rus9136/gos_tracker.git /opt/goszakup/app
cd /opt/goszakup/app
```

Дальше все `sudo -u goszakup ...` запускаются из `/opt/goszakup/app`.

---

## 3. venv и зависимости

```bash
sudo -u goszakup python3.11 -m venv /opt/goszakup/app/.venv
sudo -u goszakup /opt/goszakup/app/.venv/bin/pip install --upgrade pip
sudo -u goszakup /opt/goszakup/app/.venv/bin/pip install -e /opt/goszakup/app
```

Проверка, что Python подхватился правильный:

```bash
sudo -u goszakup /opt/goszakup/app/.venv/bin/python -c "import sys; print(sys.version)"
# должно быть 3.11.x или выше
```

---

## 4. `.env` с секретами

```bash
sudo -u goszakup cp /opt/goszakup/app/env.example /opt/goszakup/app/.env
sudo -u goszakup chmod 600 /opt/goszakup/app/.env
sudo -u goszakup nano /opt/goszakup/app/.env
```

Минимум, что нужно выставить:

```env
# Обязательно — без ключа LLM-классификация молча скипается
CEREBRAS_API_KEY=csk-...

# Включаем Basic Auth (на сервере GZ_NO_AUTH ставить НЕ надо)
# GZ_NO_AUTH=

# Креды для UI. Дефолт admin/admin — обязательно поменять.
GZ_USER=admin
GZ_PASSWORD=<длинный-пароль>
```

`config.py` подхватывает `.env` через `python-dotenv` при импорте — никаких
дополнительных действий не нужно.

---

## 5. Инициализация БД и preset'ов

```bash
cd /opt/goszakup/app
sudo -u goszakup ./.venv/bin/python -m goszakup.cli init
sudo -u goszakup ./.venv/bin/python -m goszakup.cli seed-presets
```

Должны создаться:

- `data/goszakup.sqlite` (+ `-wal`/`-shm` в WAL-режиме).
- 20 preset'ов по регионам РК (`SELECT COUNT(*) FROM presets;` → 20).

Каталог `data/` создаётся автоматически.

---

## 6. Первый прогон (sanity-check)

Один регион, чтобы убедиться, что сеть и LLM работают:

```bash
sudo -u goszakup ./.venv/bin/python -m goszakup.cli run-preset 20  # Шымкент
```

Прогон занимает несколько минут (rate-limit `Crawl-delay=5s` обязательный).
В `data/logs/` появится лог; в `data/docs/<announcement_id>/` — скачанные
ТЗ-файлы. Если LLM-шаг падает с 429 — лимит Cerebras, подождать минуту.

UI можно поднять для проверки прямо в shell:

```bash
sudo -u goszakup GZ_NO_AUTH=1 ./.venv/bin/python -m uvicorn goszakup.web.app:app \
    --host 127.0.0.1 --port 8765
```

Открыть `http://<server>:8765` через SSH-туннель (`ssh -L 8765:127.0.0.1:8765 ...`),
убедиться, что лоты видны. Ctrl-C — остановить.

---

## 7. systemd-сервис для UI

`/etc/systemd/system/goszakup-web.service`:

```ini
[Unit]
Description=Goszakup Tracker — FastAPI UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=goszakup
Group=goszakup
WorkingDirectory=/opt/goszakup/app
EnvironmentFile=/opt/goszakup/app/.env
ExecStart=/opt/goszakup/app/.venv/bin/python -m uvicorn \
    goszakup.web.app:app --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=5

# Защита процесса (приложение не нуждается в root/системных правах)
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/goszakup/app/data
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Запуск:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now goszakup-web.service
sudo systemctl status goszakup-web.service
# логи:
sudo journalctl -u goszakup-web.service -f
```

UI слушает только на `127.0.0.1` — снаружи проксируем через nginx (см. п. 9).

---

## 8. Ежедневный прогон через systemd-timer

Вместо launchd/cron — systemd-timer. Один writer-процесс одновременно
(CLAUDE.md: запускать `daily` параллельно с другими запрещено,
WAL разруливает чтения, но писатели сериализуются).

`/etc/systemd/system/goszakup-daily.service`:

```ini
[Unit]
Description=Goszakup Tracker — ежедневный обход всех preset'ов
# Не запускать, если ещё идёт предыдущий прогон или ручная переиндексация.
ConditionPathExists=!/opt/goszakup/app/data/.daily.lock

[Service]
Type=oneshot
User=goszakup
Group=goszakup
WorkingDirectory=/opt/goszakup/app
EnvironmentFile=/opt/goszakup/app/.env
ExecStartPre=/usr/bin/touch /opt/goszakup/app/data/.daily.lock
ExecStart=/opt/goszakup/app/.venv/bin/python -m goszakup.cli daily
ExecStopPost=/bin/rm -f /opt/goszakup/app/data/.daily.lock

# Прогон может идти долго (20 регионов × сетевые задержки).
TimeoutStartSec=4h
```

`/etc/systemd/system/goszakup-daily.timer`:

```ini
[Unit]
Description=Goszakup Tracker — таймер для daily

[Timer]
# 06:00 локального времени сервера. Поменять под нужный TZ.
OnCalendar=*-*-* 06:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

Активация:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now goszakup-daily.timer
# когда запустится в следующий раз
systemctl list-timers goszakup-daily.timer
# ручной прогон сейчас
sudo systemctl start goszakup-daily.service
sudo journalctl -u goszakup-daily.service -f
```

Если оставить дефолтный логгер `data/logs/*.log` через CLI — он не нужен;
вывод systemd идёт в journal. Старые `data/logs/launchd-*.log` — это
артефакты mac-окружения, на сервере не используются.

---

## 9. nginx + HTTPS (опционально, но рекомендуется)

UI bind'ится на `127.0.0.1` — снаружи доступ только через nginx
с TLS и (опционально) дополнительным `auth_basic`.

`/etc/nginx/sites-available/goszakup`:

```nginx
server {
    listen 80;
    server_name goszakup.example.com;
    # HTTP → HTTPS redirect (если используете certbot, он сам впишет)
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name goszakup.example.com;

    ssl_certificate     /etc/letsencrypt/live/goszakup.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/goszakup.example.com/privkey.pem;

    client_max_body_size 16M;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;  # ручные «загрузить документы» / «переанализ» идут долго
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/goszakup /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
# certbot для бесплатного TLS:
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d goszakup.example.com
```

Дополнительный `auth_basic` поверх app-auth — на ваш вкус. Если ставите,
не забудьте про `auth_basic_user_file`, и тогда `GZ_USER/GZ_PASSWORD`
становится «второй слой».

Firewall: открыть только 22/80/443. UI-порт 8765 наружу не выпускать.

---

## 10. Бэкап

Что критично:

- `data/goszakup.sqlite` (+ `-wal`/`-shm`) — основная БД.
- `data/docs/` — скачанные ТЗ-файлы (можно перезалить, но дорого по
  rate-limit goszakup).
- `.env` — секреты (хранить отдельно, не в общем бэкапе).

SQLite-бэкап делать через `.backup`, **не** копированием файла во время
работы writer'а:

```bash
sudo -u goszakup /opt/goszakup/app/.venv/bin/python -c "
import sqlite3
src = sqlite3.connect('/opt/goszakup/app/data/goszakup.sqlite')
dst = sqlite3.connect('/opt/goszakup/backup/goszakup-$(date +%F).sqlite')
src.backup(dst); dst.close(); src.close()
"
```

Можно завернуть в отдельный systemd-timer, который стартует после
`goszakup-daily.service` (через `OnUnitInactiveSec=` или `After=`).

---

## 11. Обновление кода

```bash
sudo -u goszakup git -C /opt/goszakup/app pull --ff-only
sudo -u goszakup /opt/goszakup/app/.venv/bin/pip install -e /opt/goszakup/app
# Применить миграции схемы (см. ниже про первый запуск на существующей БД)
sudo -u goszakup /opt/goszakup/app/.venv/bin/alembic upgrade head
sudo systemctl restart goszakup-web.service
# daily.timer переподхватится сам со следующего запуска
```

Миграции теперь живут в `migrations/versions/` (Alembic). При **первом**
накате на сервер с уже существующей БД нужно один раз пометить её как
соответствующую baseline-ревизии (иначе `alembic upgrade head` упадёт,
пытаясь создать уже существующие таблицы):

```bash
sudo -u goszakup /opt/goszakup/app/.venv/bin/alembic stamp head
sudo -u goszakup /opt/goszakup/app/.venv/bin/alembic current  # должно показать ревизию
```

После этого все следующие релизы — обычный `alembic upgrade head`.
`init_db()` / `create_all()` оставлены как safety net и не мешают.
Если меняли `ANALYZER_VERSION` — следующий `daily` сам перегонит лоты с
устаревшим анализом (идемпотентность по `(analyzer_version, tz_sha256)`).

---

## 12. Траблшутинг

| Симптом | Куда смотреть |
|---|---|
| 502 от nginx | `systemctl status goszakup-web` — uvicorn упал, `journalctl -u goszakup-web -n 200` |
| `database is locked` | Кто-то запустил `daily` параллельно с `run-preset`/массовым скриптом. Проверить `pgrep -af goszakup.cli`. WAL разруливает читателей, но не writer'ов. |
| 429 от Cerebras | Free-tier лимит ~30 req/мин. `_call_llm` ретраит 5/15/30с. Если падает массово — пауза/upgrade тарифа. |
| Пустой UI после первого старта | Не сделан `seed-presets` или `daily` ещё не отработал. Проверить `SELECT COUNT(*) FROM lots;`. |
| HTTP 401 при заходе | `GZ_NO_AUTH` не выставлен, креды из `GZ_USER/GZ_PASSWORD` отличаются от тех, что вводите. |
| Документы не качаются | goszakup мог сменить ajax-механику. Смотреть `WARNING modal …` в journal; см. CLAUDE.md «Скрытые подводные камни». |

Полезные команды:

```bash
# Размер БД и кол-во строк
sudo -u goszakup sqlite3 /opt/goszakup/app/data/goszakup.sqlite \
    "SELECT 'lots', COUNT(*) FROM lots UNION ALL SELECT 'docs', COUNT(*) FROM documents;"

# Последние прогоны
sudo -u goszakup sqlite3 /opt/goszakup/app/data/goszakup.sqlite \
    "SELECT id, preset_id, started_at, finished_at, status FROM scrape_runs ORDER BY id DESC LIMIT 10;"

# Логи UI и daily
sudo journalctl -u goszakup-web.service --since "1 hour ago"
sudo journalctl -u goszakup-daily.service --since today
```
