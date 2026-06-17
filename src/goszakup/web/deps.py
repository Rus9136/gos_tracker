"""Зависимости FastAPI: сессия БД, helpers для шаблонов."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC
from decimal import Decimal

from sqlalchemy.orm import Session

from ..db.engine import SessionLocal


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def format_amount(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    try:
        # Decimal поддерживает `:,.0f` нативно — этот же код работает на
        # float/int/Decimal без преобразований.
        return f"{value:,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def format_dt(value) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def format_iso(value) -> str:
    # ISO-8601 для data-атрибутов. Метки хранятся в UTC (правило #12), но
    # SQLite-фолбэк отдаёт их naive — без явного offset'а JS `new Date(...)`
    # принял бы их за локальное время. Поэтому naive трактуем как UTC и всегда
    # печатаем со смещением (`+00:00`). Пусто — чтобы шаблон отрисовал прочерк.
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def format_compact(value: Decimal | float | int | None) -> str:
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        s = f"{n / 1_000_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{s} млрд"
    if abs_n >= 1_000_000:
        s = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{s} млн"
    if abs_n >= 1_000:
        return f"{n / 1_000:.0f} тыс"
    return f"{n:.0f}"
