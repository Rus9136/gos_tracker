# REMEDIATION_LOG — сессия Гейт 1 (автоподача) + быстрые фиксы

Дата: 2026-07-15. Ветка: `feat/tender-autosubmit`. База: `2c9809a`.
Задание: верификация 8 спорных утверждений аудита + 2 быстрых фикса + полный
Гейт 1. Каждая находка — атомарный коммит; где воспроизводимо — красный тест
первым. Итог: **206 тестов зелёные** (было 143), +63 новых теста.

## Что сделано (12 коммитов)

**Быстрые фиксы:**
- `41e0dfa` **P0-1** fail-fast `SECRET_KEY`: web (lifespan) и worker (dramatiq
  `after_worker_boot`) падают на дефолтном/пустом ключе, кроме `GZ_NO_AUTH=1`/
  `GZ_TEST_MODE=1`. `env.example` дополнен `GZ_SECRET_KEY`/`GZ_VAULT_MASTER_KEY`.
- `e54ac6e` **P0-8** IDOR `/document/{id}/download`: scope-проверка через лоты
  объявления, вне scope 404.

**Гейт 1:**
- `85b812c` **P0-2** `goszakup_autosubmit` дописан в `--queues` systemd + импорт
  actor'а в `queue/actors.py` (без него он не регистрировался в брокере вообще).
  Регресс-тест: systemd-очереди ⊇ объявленных в коде.
- `5e2fc3b` **P0-3a** claim подачи (`PLANNED→DISPATCHING` + commit) до
  `agent.dispatch()`, `FOR UPDATE SKIP LOCKED` на PG, транзакция на подачу.
- `a4b5650` **P0-3b** дедуп `/run` по `submission_id` на агенте (реестр + Lock).
- `67f0c06` **P0-3c** `fire()` ретраит только ConnectError/ConnectTimeout;
  ReadTimeout после отправки → UNKNOWN без ретрая.
- `2bc2fd0` **P0-apply** `apply_result` forward-only: терминальный статус
  (в т.ч. CONFIRMED) не перезаписывается поздней/повторной доставкой.
- `eb7aebf` **P0-channel** обязательный `GZ_AGENT_TOKEN` (agent fail-closed) +
  запрет plain-http без `GZ_AUTOSUBMIT_AGENT_ALLOW_HTTP` в `AgentClient`.
- `26e2918` **P0/P1-5** `deadline_guard` реализован в `agent/timing.py` и вызван
  на обеих сторонах (диспетчер → SKIPPED; агент не стреляет после close_at).
- `1cba967` **P1** проверка срока лицензии Tumar в `jobs/health` (алерт админам);
  активна только когда автоподача сконфигурирована (`GZ_AUTOSUBMIT_AGENT_URL`).
- `9013a80` **verify-7** секреты автоподачи скрыты от Sentry: `field(repr=False)`
  на `RunRequest`/`DecryptedCredential`/`LotBid` + `EventScrubber` с denylist.
- `6b057c8` **P0-9** тесты `vault/` (round-trip, InvalidTag, неверный/нет ключа,
  credentials).

## Отложено (DEFERRED)
- **fire() reconcile** вместо UNKNOWN: нужен эндпоинт goszakup «заявка принята?»,
  которого нет в recon. Сейчас — UNKNOWN + алерт (не задваиваем). Доделать при
  наличии данных об API.

## Новые ENV-переменные
- `GZ_AGENT_DEV=1` — dev-режим агента без токена (иначе agent не стартует).
- `GZ_AUTOSUBMIT_AGENT_ALLOW_HTTP` — CSV хостов, которым разрешён plain-http
  (приватный tailnet). Пусто = только https.
- `GZ_TUMAR_LICENSE_EXPIRES` (дефолт `2026-07-01`), `GZ_TUMAR_LICENSE_WARN_DAYS`
  (дефолт 14). Пустая дата отключает проверку.

## Проверить руками на проде (перед включением автоподачи)
1. **Live systemd-юнит воркера**: дописать `goszakup_autosubmit` в `--queues`
   работающего `/etc/systemd/system/goszakup-worker.service` (в репо поправлен
   только шаблон `scripts/systemd/`), `daemon-reload` + `restart`.
2. **`GZ_SECRET_KEY`** уже в проде `.env` — после этого релиза web/worker
   потребуют его обязательно. Убедиться, что задан (иначе сервис не поднимется).
3. **`GZ_AGENT_TOKEN`/`GZ_AUTOSUBMIT_AGENT_TOKEN`** — задать на обоих узлах перед
   включением автоподачи (agent теперь fail-closed без токена).
4. **Реальный срок лицензии Tumar**: план говорит 2026-07-01 — сегодня уже
   истёк. Проверить фактический срок и выставить `GZ_TUMAR_LICENSE_EXPIRES`
   (или продлить лицензию). При сконфигурированной автоподаче health сразу
   зальёт алерт.
5. **Таймер `goszakup-autosubmit.timer` НЕ включать** — submit-agent
   (визард/Tumar/NCALayer) ещё `TODO(recon)`, автоподача не готова к бою.
6. **`GZ_AUTOSUBMIT_AGENT_URL`** должен быть `https://` либо хост в
   `GZ_AUTOSUBMIT_AGENT_ALLOW_HTTP` — иначе диспетчер откажется слать секреты.

## Не трогалось (вне скоупа Гейта 1)
- Гейт 3 (IDOR/CSRF/prompt-injection) — см. `ARCHITECTURE_AUDIT.md`, раздел OPEN.
- 6 предсуществующих ruff-замечаний (import-order/whitespace) в
  `classify/llm.py`, `scraper/announce.py`, `tests/conftest.py`,
  `tests/test_rules.py`, `tests/test_simhash.py` — не мои файлы, не «улучшал».

---

# Гейт 2 (масштаб до 800K) — 15 коммитов

Дата: 2026-07-15. Все 14 задач Гейта 2 закрыты. Тесты: **246 passed** (+31 к
Гейту 1). Каждая находка — атомарный коммит; где воспроизводимо — красный тест.

**Производительность/схема:** составные индексы (`42914ae`), `/matched` LIMIT в
SQL (`87391c8`), pg_trgm-индексы поиска (`4d5eeef`), init_db штампует Alembic head
(`e49cb01`), `/expenses` агрегация в SQL (`10e15b1`).
**Гонки данных:** savepoint при insert лота (`e81d93f`), частичный уникальный
индекс + дедуп организаций (`3e40b20`).
**Тихие деградации → сигналы:** неизвестный статус (`ab0e690`), деградация
парсера (`13e84f3`), health-проверки Redis/диска (`25a076c`).
**Отказоустойчивость:** self-healing reconcile заглушек (`3c0e6ff`), ретрай
транзиентных LLM-ошибок (`2ab8013`), rate-limit держит лок на время запроса
(`a51bc8e`), async telegram_webhook (`7f9e8c8`), retention data/docs (`e7bca36`).

## Проверить руками на проде (деплой Гейта 2)
1. **`alembic upgrade head` ОБЯЗАТЕЛЕН** перед рестартом — добавляет составные и
   pg_trgm-индексы и дедупит организации. На существующей БД `create_all` индексы
   НЕ добавляет. Миграции (включая дедуп FK + pg_trgm) проверены на PG16 во
   временном docker-контейнере. Запускать в низкий трафик (CREATE INDEX без
   CONCURRENTLY кратко блокирует запись; на текущем объёме — секунды).
2. **`CREATE EXTENSION pg_trgm`** — миграция делает `IF NOT EXISTS`; роль
   `goszakup` должна иметь право. Если нет — DBA создаёт расширение заранее.
3. Новые ENV (опционально, есть дефолты): `GZ_HEALTH_DISK_MIN_FREE_GB` (1),
   `GZ_DOCS_RETENTION_DAYS` (90).
4. Retention и health-проверки Redis/диска включаются сами (ежечасный
   expire-таймер). Reconcile заглушек — тоже оттуда.

## Отложено (Гейт 2)
- Точная глубина очередей Dramatiq в health — пропущена намеренно (требует
  привязки к внутреннему Redis-layout dramatiq, хрупко). Косвенно покрыто
  проверкой Redis + давностью матча.
