# systemd-юниты для gost.salemsoft.kz

Скопировать на сервер и активировать. Пути в юнитах под реальную раскладку
прода (`/home/rus/projects/gos_tracker`, юзер `rus`).

## Установка backup-юнитов

```bash
sudo cp scripts/systemd/goszakup-backup.service /etc/systemd/system/
sudo cp scripts/systemd/goszakup-backup.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now goszakup-backup.timer

# Когда сработает в след. раз
systemctl list-timers goszakup-backup.timer
# Ручной прогон сейчас
sudo systemctl start goszakup-backup.service
sudo journalctl -u goszakup-backup.service --since today
```

Бэкапы складываются в `data/backups/goszakup-YYYY-MM-DDTHH-MM-SS.sqlite`,
последние `KEEP_LAST=14` сохраняются, более старые автоматически удаляются.

## Скрипт `backup_sqlite.py`

Можно запустить вручную без systemd:

```bash
.venv/bin/python -m scripts.backup_sqlite
ls -lt data/backups/ | head
```

Использует `sqlite3.Connection.backup()` — атомарный online backup,
безопасно при работающем uvicorn (в WAL-режиме).

## Установка worker-юнита (Phase 3)

Требует Redis на хосте (`apt install redis-server`) и `GZ_REDIS_URL` в `.env`
(дефолт `redis://localhost:6379/0`).

```bash
# Установить redis (как root)
sudo apt install -y redis-server
sudo systemctl enable --now redis-server

# Поставить worker-юнит
sudo cp scripts/systemd/goszakup-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now goszakup-worker.service

sudo journalctl -u goszakup-worker.service -f
```

После активации:
- `goszakup-daily.timer` шлёт `daily_actor` в очередь (через `cli daily`,
  без `--sync`). Worker подбирает.
- `/ingest/run` шлёт `ingest_actor.send(...)` — переживает рестарт uvicorn.
- 3-стейдж пайплайн (listing → detail → llm) исполняется параллельно
  с глобальным rate-limit на goszakup через Redis-mutex.

ВАЖНО: при добавлении actor'а с новой очередью допиши её в `--queues` воркера
(`goszakup-worker.service`), иначе задачи молча копятся. Текущий список включает
`goszakup_autosubmit` (правило #19).

## Установка autosubmit-юнитов (правило #19, Phase 2+)

Диспетчер автоподачи. Включать **только когда готов Windows submit-agent** и в
`.env` заданы `GZ_AUTOSUBMIT_AGENT_URL` (+ `GZ_VAULT_MASTER_KEY`,
`GZ_AUTOSUBMIT_INGEST_TOKEN`). Без `GZ_AUTOSUBMIT_AGENT_URL` актор тихо выходит —
безопасно держать таймер выключенным до готовности agent'а.

```bash
sudo cp scripts/systemd/goszakup-autosubmit.service /etc/systemd/system/
sudo cp scripts/systemd/goszakup-autosubmit.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now goszakup-autosubmit.timer
systemctl list-timers goszakup-autosubmit.timer
```

Ежеминутно: `cli autosubmit-dispatch --enqueue` → `autosubmit_dispatch_actor`
(очередь `goszakup_autosubmit`) шлёт агенту PLANNED-подачи, открывающиеся в
ближайший `GZ_AUTOSUBMIT_WARMUP_LEAD` (дефолт 300с). Агент отчитывается обратно
на `POST /autosubmit/result`.

