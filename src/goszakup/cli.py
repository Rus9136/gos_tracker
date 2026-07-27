"""Командная строка для управления приложением.

Примеры:
    python -m goszakup.cli init                # создать таблицы
    python -m goszakup.cli seed-presets        # создать дефолтные preset'ы по регионам
    python -m goszakup.cli presets             # список preset'ов
    python -m goszakup.cli run-preset 5        # прогнать один preset по id
    python -m goszakup.cli run-once            # тестовый прогон без записи preset'а
    python -m goszakup.cli daily               # обойти все активные preset'ы
    python -m goszakup.cli stats               # сводка по БД
"""

from __future__ import annotations

import logging

import typer
from sqlalchemy import func, select

from .classify.llm import ANALYZER_VERSION, analyze_and_save
from .db.engine import SessionLocal, init_db
from .db.models import Announcement, Document, Lot, LotAnalysis, Organization, Preset, ScrapeRun
from .jobs.daily import run_all_active
from .jobs.run_preset import run_preset
from .jobs.seed import seed_regional_presets
from .observability import setup_sentry
from .scraper.search import SearchParams
from .scraper.statuses import ACTUAL_STATUSES

setup_sentry("cli")

app = typer.Typer(add_completion=False, help=__doc__)


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level="DEBUG" if verbose else "INFO",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def init() -> None:
    """Создать таблицы в SQLite."""
    init_db()
    typer.echo("DB initialised")


@app.command("seed-presets")
def seed_presets() -> None:
    """Создать по одному preset'у на регион РК (idempotent)."""
    n = seed_regional_presets()
    typer.echo(f"seeded {n} new presets")


@app.command()
def presets() -> None:
    """Список preset'ов."""
    init_db()
    with SessionLocal() as s:
        for p in s.scalars(select(Preset).order_by(Preset.id)):
            active = "✓" if p.active else " "
            typer.echo(
                f"  [{active}] #{p.id:>3} {p.name:<40} kato={p.kato} "
                f"amount={p.amount_from}..{p.amount_to or '∞'} statuses={p.status_codes}"
            )


@app.command("run-preset")
def run_preset_cmd(
    preset_id: int = typer.Argument(...),
    download_docs: bool = typer.Option(True, "--docs/--no-docs"),
    verbose: bool = typer.Option(False, "-v"),
) -> None:
    """Прогнать один preset."""
    _setup_logging(verbose)
    init_db()
    with SessionLocal() as s:
        preset = s.get(Preset, preset_id)
        if not preset:
            typer.echo(f"preset {preset_id} not found", err=True)
            raise typer.Exit(1)
    run = run_preset(preset=preset, download_docs=download_docs)
    typer.echo(
        f"run #{run.id}: listing={run.listing_count} new={run.new_lots} "
        f"updated={run.updated_lots} docs={run.new_documents} errors={run.errors}"
    )


@app.command("run-once")
def run_once(
    kato: str = typer.Option(..., help="код КАТО"),
    amount_from: int = typer.Option(500_000),
    amount_to: int | None = typer.Option(None),
    status: list[int] = typer.Option(None, "--status", help="код статуса (можно повторять)"),
    actual_only: bool = typer.Option(False, "--actual"),
    it_only: list[str] = typer.Option(
        None, "--it", help="оставить только эти IT-категории (можно повторять)"
    ),
    docs: bool = typer.Option(True, "--docs/--no-docs"),
    listing_only: bool = typer.Option(
        False, "--listing-only", help="только листинг (без деталей/документов/LLM)"
    ),
    verbose: bool = typer.Option(False, "-v"),
) -> None:
    """Разовый прогон без сохранения preset'а."""
    _setup_logging(verbose)
    codes = list(status or [])
    if actual_only and not codes:
        codes = list(ACTUAL_STATUSES)
    params = SearchParams(
        kato=kato, amount_from=amount_from, amount_to=amount_to, status_codes=codes
    )
    run = run_preset(
        params=params,
        it_categories=it_only,
        download_docs=docs,
        listing_only=listing_only,
    )
    typer.echo(
        f"run #{run.id}: listing={run.listing_count} new={run.new_lots} "
        f"updated={run.updated_lots} docs={run.new_documents} errors={run.errors}"
    )


@app.command()
def daily(
    sync: bool = typer.Option(
        False, "--sync", help="Прогнать всё в этом процессе, без очереди."
    ),
) -> None:
    """Запустить ежедневный обход всех активных preset'ов.

    По умолчанию отправляет `daily_actor` в очередь — workers подберут.
    `--sync` — старый поведение (всё в одном процессе через `run_all_active`),
    полезно для CI/смоук-прогонов без поднятого Redis.
    """
    _setup_logging(verbose=False)
    if sync:
        run_all_active()
        return
    from .queue.actors import daily_actor
    msg = daily_actor.send()
    typer.echo(f"enqueued daily_actor (message_id={msg.message_id})")


@app.command("contracts-sync")
def contracts_sync(
    days: int = typer.Option(
        ..., "--days", help="Окно бэкофилла: договоры за N последних дней."
    ),
    sync: bool = typer.Option(
        False, "--sync", help="Прогнать в этом процессе, без очереди."
    ),
) -> None:
    """Синк договоров/победителей из API OWS за произвольное окно.

    Штатно contracts_sync_actor запускается из daily по водяному знаку;
    команда — для ручного бэкофилла (например, за период регрессии).
    """
    _setup_logging(verbose=True)
    if sync:
        from datetime import UTC, datetime, timedelta

        from .api.client import OwsClient
        from .jobs.contracts import sync_contracts

        init_db()
        dt_to = datetime.now(UTC)
        with SessionLocal() as s:
            stats = sync_contracts(
                s, OwsClient(), dt_from=dt_to - timedelta(days=days), dt_to=dt_to
            )
        typer.echo(
            f"scanned={stats.scanned} matched={stats.matched} "
            f"created={stats.created} updated={stats.updated} "
            f"winners={stats.winners_filled}"
        )
        return
    from .queue.actors import contracts_sync_actor

    msg = contracts_sync_actor.send(days)
    typer.echo(f"enqueued contracts_sync_actor (message_id={msg.message_id})")


@app.command()
def expire(
    sync: bool = typer.Option(
        False, "--sync", help="Прогнать в этом процессе, без очереди."
    ),
) -> None:
    """Снять с «актуальных» лоты с истёкшим сроком приёма заявок.

    По умолчанию отправляет `expire_actor` в очередь (так гоняет ежечасный
    systemd-таймер). `--sync` — выполнить сразу в этом процессе (для ручного
    прогона / CI без Redis). К goszakup не ходит — только UPDATE по БД.
    """
    _setup_logging(verbose=False)
    if sync:
        from .jobs.expire import expire_actual_lots
        init_db()
        with SessionLocal() as s:
            n = expire_actual_lots(s)
        typer.echo(f"снято с актуальных: {n}")
        return
    from .queue.actors import expire_actor
    msg = expire_actor.send()
    typer.echo(f"enqueued expire_actor (message_id={msg.message_id})")


@app.command("health-check")
def health_check(
    notify: bool = typer.Option(
        True, "--notify/--no-notify", help="Слать алерт в Telegram админам."
    ),
) -> None:
    """Проверить, жив ли LLM-контур; при поломке — алерт админам в Telegram.

    Так гоняет ежечасный systemd-таймер. Существует потому, что правило #7
    гасит ошибки LLM намеренно: без активной проверки отказ провайдера виден
    только в journal (402 от Cerebras однажды тихо гасил матчинг 15 дней).
    Exit code 1 при проблеме — чтобы systemd пометил юнит failed.
    """
    _setup_logging(verbose=False)
    from .jobs.health import run_health_check

    with SessionLocal() as s:
        problems = run_health_check(s, notify=notify)
    if not problems:
        typer.echo("health: ок")
        return
    for p in problems:
        typer.echo(p)
    raise typer.Exit(1)


@app.command()
def reanalyze(
    lot_id: int | None = typer.Option(None, "--lot-id", help="один лот по id"),
    limit: int = typer.Option(50, "--limit", help="максимум лотов за запуск"),
    force: bool = typer.Option(
        False, "--force", help="игнорировать sha256+version (перегнать заведомо)"
    ),
    verbose: bool = typer.Option(False, "-v"),
) -> None:
    """Прогнать LLM-классификацию по скачанным лотам без обращения к goszakup."""
    _setup_logging(verbose)
    init_db()
    from sqlalchemy.orm import selectinload

    with SessionLocal() as s:
        stmt = (
            select(Lot)
            .where(Lot.it_category.isnot(None))
            .options(
                selectinload(Lot.customer),
                selectinload(Lot.analysis),
                selectinload(Lot.announcement).selectinload(Announcement.documents),
            )
        )
        if lot_id is not None:
            stmt = stmt.where(Lot.id == lot_id)
        candidates = s.scalars(stmt).all()

        if force:
            # Сбрасываем версии — analyze_and_save посчитает их устаревшими.
            for lot in candidates:
                if lot.analysis:
                    lot.analysis.analyzer_version = "__force__"
            s.flush()

        done = 0
        skipped = 0
        for lot in candidates:
            if done >= limit:
                break
            if analyze_and_save(s, lot):
                s.commit()
                done += 1
                typer.echo(
                    f"  ✓ lot {lot.id}: {lot.analysis.dev_category} "
                    f"({lot.analysis.analysis_confidence})"
                )
            else:
                skipped += 1
        typer.echo(
            f"reanalyzed {done}, skipped {skipped}, total candidates {len(candidates)}"
        )


@app.command()
def stats() -> None:
    """Сводка по БД."""
    init_db()
    with SessionLocal() as s:
        n_lots = s.scalar(select(func.count(Lot.id))) or 0
        n_actual = s.scalar(select(func.count(Lot.id)).where(Lot.is_actual.is_(True))) or 0
        n_anno = s.scalar(select(func.count(Announcement.id))) or 0
        n_org = s.scalar(select(func.count(Organization.id))) or 0
        n_docs = s.scalar(select(func.count(Document.id))) or 0
        n_runs = s.scalar(select(func.count(ScrapeRun.id))) or 0
        n_analyses = s.scalar(select(func.count(LotAnalysis.id))) or 0
        total_amount = s.scalar(select(func.sum(Lot.plan_amount))) or 0
        typer.echo(f"lots:           {n_lots:>8,}  (актуальных: {n_actual:,})")
        typer.echo(f"announcements:  {n_anno:>8,}")
        typer.echo(f"organizations:  {n_org:>8,}")
        typer.echo(f"documents:      {n_docs:>8,}")
        typer.echo(f"lot analyses:   {n_analyses:>8,}  (analyzer={ANALYZER_VERSION})")
        typer.echo(f"scrape runs:    {n_runs:>8,}")
        typer.echo(f"total plan ₸:   {total_amount:>16,.0f}")


@app.command("create-user")
def create_user(
    username: str,
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    admin: bool = typer.Option(False, "--admin", help="сделать администратором"),
) -> None:
    """Завести пользователя (бутстрап первого админа — с --admin)."""
    from .db.models import User
    from .web.auth import hash_password

    init_db()
    with SessionLocal() as s:
        if s.scalar(select(User).where(User.username == username)):
            typer.echo(f"пользователь {username!r} уже существует")
            raise typer.Exit(1)
        s.add(
            User(
                username=username,
                password_hash=hash_password(password),
                is_admin=admin,
                is_active=True,
            )
        )
        s.commit()
    typer.echo(f"создан {'админ' if admin else 'пользователь'} {username!r}")


@app.command("list-users")
def list_users() -> None:
    """Список пользователей."""
    from .db.models import User

    init_db()
    with SessionLocal() as s:
        for u in s.scalars(select(User).order_by(User.id)):
            role = "admin" if u.is_admin else "user "
            active = "✓" if u.is_active else "✗"
            regions = ",".join(u.regions) if u.regions else "—"
            typer.echo(f"  #{u.id:>3} [{active}] {role} {u.username:<24} регионы={regions}")


@app.command("telegram-set-webhook")
def telegram_set_webhook(
    drop: bool = typer.Option(False, "--drop", help="снять вебхук вместо установки"),
) -> None:
    """Зарегистрировать Telegram-вебхук кнопки «Подробнее» (одноразово).

    Требует GZ_TELEGRAM_BOT_TOKEN и GZ_TELEGRAM_WEBHOOK_SECRET в .env;
    URL берётся из GZ_PUBLIC_BASE_URL + /telegram/webhook.
    """
    from .config import PUBLIC_BASE_URL, TELEGRAM_WEBHOOK_SECRET
    from .notify.telegram import delete_webhook, set_webhook

    if drop:
        ok, err = delete_webhook()
        typer.echo("вебхук снят" if ok else f"ошибка: {err}")
        raise typer.Exit(0 if ok else 1)

    if not TELEGRAM_WEBHOOK_SECRET:
        typer.echo("GZ_TELEGRAM_WEBHOOK_SECRET не задан (сгенерируйте: openssl rand -hex 32)", err=True)
        raise typer.Exit(1)
    url = f"{PUBLIC_BASE_URL}/telegram/webhook"
    ok, err = set_webhook(url, TELEGRAM_WEBHOOK_SECRET)
    typer.echo(f"вебхук установлен: {url}" if ok else f"ошибка: {err}")
    raise typer.Exit(0 if ok else 1)


@app.command("set-password")
def set_password(
    username: str,
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Сменить пароль пользователю."""
    from .db.models import User
    from .web.auth import hash_password

    init_db()
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username == username))
        if u is None:
            typer.echo(f"нет пользователя {username!r}")
            raise typer.Exit(1)
        u.password_hash = hash_password(password)
        s.commit()
    typer.echo(f"пароль для {username!r} обновлён")


@app.command("queries")
def queries_list() -> None:
    """Список семантических запросов пользователей (UserQuery)."""
    from .db.models import User, UserQuery

    init_db()
    with SessionLocal() as s:
        rows = s.execute(
            select(UserQuery, User)
            .join(User, UserQuery.user_id == User.id)
            .order_by(UserQuery.id)
        ).all()
        if not rows:
            typer.echo("нет запросов")
            return
        for q, u in rows:
            active = "✓" if q.active else "✗"
            preview = (q.text or "").replace("\n", " ")[:60]
            typer.echo(
                f"  #{q.id:>3} [{active}] v{q.version} {u.username:<16} "
                f"{q.name!r}: {preview}"
            )


@app.command("match-backfill")
def match_backfill(
    query_id: int,
    limit: int = typer.Option(2000, help="Максимум лотов за прогон"),
    sync: bool = typer.Option(
        False, "--sync", help="Считать матчи здесь, без очереди/воркера"
    ),
) -> None:
    """Сопоставить запрос с актуальными лотами в scope (создание/правка запроса)."""
    from .jobs.match import backfill_query

    init_db()
    n = backfill_query(query_id, limit=limit, sync=sync)
    mode = "посчитано" if sync else "поставлено в очередь"
    typer.echo(f"query #{query_id}: {n} лотов {mode}")


@app.command("create-credential")
def create_credential_cmd(
    label: str,
    p12_path: str = typer.Option(..., "--p12", help="Путь к .p12/.pfx ключу клиента"),
    iin_bin: str = typer.Option(None, "--iin-bin", help="ИИН/БИН клиента (для аудита)"),
    notes: str = typer.Option(None, "--notes"),
) -> None:
    """Завести зашифрованную учётку клиента для автоподачи (KeyVault, §8).

    Требует GZ_VAULT_MASTER_KEY. Пароль портала и PIN спрашиваются интерактивно —
    в argv/историю не попадают.
    """
    from pathlib import Path

    from .vault.credentials import create_credential

    p12_bytes = Path(p12_path).read_bytes()
    portal_password = typer.prompt("Пароль учётки портала goszakup", hide_input=True)
    key_pin = typer.prompt("PIN ключа (Enter — пропустить)", default="", hide_input=True) or None

    init_db()
    with SessionLocal() as session:
        cred = create_credential(
            session,
            label=label,
            p12_bytes=p12_bytes,
            portal_password=portal_password,
            iin_bin=iin_bin,
            key_pin=key_pin,
            notes=notes,
        )
        session.commit()
        typer.echo(f"учётка #{cred.id} '{label}' создана (секреты зашифрованы)")


@app.command("plan-submission")
def plan_submission_cmd(
    credential_id: int,
    anno_id: int,
    open_at: str = typer.Option(
        None,
        help="ISO8601 момента открытия приёма. По умолчанию — application_start "
        "объявления (TrdBuy.startDate из OWS)",
    ),
    lot: list[str] = typer.Option(
        ..., "--lot", help="lot_id:price (можно несколько --lot)"
    ),
    close_at: str = typer.Option(
        None, help="ISO8601 дедлайна приёма. По умолчанию — application_end объявления"
    ),
    anno_number: str = typer.Option(None, help="Человекочитаемый номер объявления"),
) -> None:
    """Запланировать автоподачу: лоты+цены (цена шифруется — sealed-bid), open_at."""
    import json
    from datetime import datetime

    from .db.models import Announcement, Submission
    from .vault.crypto import encrypt_str

    bids, lot_ids = [], []
    for item in lot:
        lid, price = item.split(":", 1)
        bids.append({"lot_id": int(lid), "price": price.strip()})
        lot_ids.append(int(lid))
    bid_enc, bid_nonce = encrypt_str(json.dumps(bids))

    init_db()
    with SessionLocal() as session:
        anno = session.get(Announcement, anno_id)
        open_dt = datetime.fromisoformat(open_at) if open_at else (
            anno.application_start if anno else None
        )
        if open_dt is None:
            # Без open_at гонка бессмысленна: агент не знает, когда стрелять.
            raise typer.BadParameter(
                f"у объявления {anno_id} пуст application_start — укажите --open-at "
                "явно (или обновите деталь объявления)"
            )
        close_dt = datetime.fromisoformat(close_at) if close_at else (
            anno.application_end if anno else None
        )
        sub = Submission(
            credential_id=credential_id,
            anno_id=anno_id,
            anno_number=anno_number or (anno.number if anno else None),
            lot_ids=lot_ids,
            bid_enc=bid_enc,
            bid_nonce=bid_nonce,
            open_at=open_dt,
            close_at=close_dt,
        )
        session.add(sub)
        session.commit()
        typer.echo(
            f"подача #{sub.id} запланирована: anno={anno_id}, лотов={len(lot_ids)}, "
            f"open_at={open_dt.isoformat()}"
            f"{'' if open_at else ' (из объявления)'}"
        )


@app.command("autosubmit-dispatch")
def autosubmit_dispatch_cmd(
    sync: bool = typer.Option(
        True, "--sync/--enqueue", help="Считать здесь (--sync) или поставить актора"
    ),
) -> None:
    """Разослать агенту PLANNED-подачи, открывающиеся в ближайший warmup-lead."""
    from . import config

    init_db()
    if not sync:
        from .queue.autosubmit import autosubmit_dispatch_actor

        autosubmit_dispatch_actor.send()
        typer.echo("autosubmit dispatch: поставлено в очередь")
        return

    if not config.AUTOSUBMIT_AGENT_URL:
        typer.echo("GZ_AUTOSUBMIT_AGENT_URL не задан — нечему слать RunRequest")
        raise typer.Exit(1)

    from .autosubmit.agent_client import AgentClient
    from .autosubmit.scheduler import dispatch_due_submissions

    with SessionLocal() as session:
        armed = dispatch_due_submissions(
            session,
            AgentClient(config.AUTOSUBMIT_AGENT_URL, token=config.AUTOSUBMIT_AGENT_TOKEN),
            warmup_lead=config.AUTOSUBMIT_WARMUP_LEAD,
        )
    typer.echo(f"autosubmit dispatch: отправлено {len(armed)} → ARMED {armed}")


if __name__ == "__main__":
    app()
