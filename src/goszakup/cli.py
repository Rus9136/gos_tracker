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
from typing import Optional

import typer
from sqlalchemy import func, select

from .classify.llm import ANALYZER_VERSION, analyze_and_save
from .db.engine import SessionLocal, init_db
from .db.models import Announcement, Document, Lot, LotAnalysis, Organization, Preset, ScrapeRun
from .jobs.daily import run_all_active
from .jobs.run_preset import run_preset
from .jobs.seed import seed_regional_presets
from .scraper.search import SearchParams
from .scraper.statuses import ACTUAL_STATUSES

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
    amount_to: Optional[int] = typer.Option(None),
    status: list[int] = typer.Option(None, "--status", help="код статуса (можно повторять)"),
    actual_only: bool = typer.Option(False, "--actual"),
    it_only: list[str] = typer.Option(
        None, "--it", help="оставить только эти IT-категории (можно повторять)"
    ),
    docs: bool = typer.Option(True, "--docs/--no-docs"),
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
    run = run_preset(params=params, it_categories=it_only, download_docs=docs)
    typer.echo(
        f"run #{run.id}: listing={run.listing_count} new={run.new_lots} "
        f"updated={run.updated_lots} docs={run.new_documents} errors={run.errors}"
    )


@app.command()
def daily() -> None:
    """Обойти все активные preset'ы (то, что вызывается ежедневным таймером)."""
    _setup_logging(verbose=False)
    run_all_active()


@app.command()
def reanalyze(
    lot_id: Optional[int] = typer.Option(None, "--lot-id", help="один лот по id"),
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


if __name__ == "__main__":
    app()
