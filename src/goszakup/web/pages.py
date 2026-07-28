"""Реестр пользовательских вкладок UI и проверка доступа по роли.

Роль (`db.models.Role`) — набор ключей отсюда. Ограничиваются только
пользовательские вкладки: системный раздел («Система») ролям не раздаётся,
он жёстко за `is_admin` (иначе галочка в роли превратилась бы в выдачу
админ-прав). Пользователь без роли и админ видят все вкладки — так ввод
ролей не меняет доступ существующих пользователей.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from ..db.models import User
from .auth import require_user

# (ключ, название, путь). Порядок = порядок в сайдбаре; он же используется
# для редиректа с "/" на первую разрешённую вкладку, если дашборд закрыт.
PAGES: list[tuple[str, str, str]] = [
    ("dashboard", "Дашборд", "/"),
    ("actual", "Актуальные", "/actual"),
    ("past", "Прошедшие", "/past"),
    ("starred", "Избранное", "/starred"),
    ("organizations", "Заказчики", "/organizations"),
    ("matched", "Подходящие", "/matched"),
    ("queries", "Мои запросы", "/queries"),
    ("settings", "Настройки", "/settings"),
]
PAGE_KEYS = {key for key, _, _ in PAGES}

# Системные вкладки, которые МОЖНО выдать ролью не-админу. В отличие от
# PAGES они НЕ входят в дефолт «пользователь без роли» — только явная
# галочка в роли (или is_admin). Управление доступом (/users, /roles) и
# остальной системный раздел сюда не выносить.
SYSTEM_PAGES: list[tuple[str, str, str]] = [
    ("ingest", "Догрузка по БИН", "/ingest"),
]
SYSTEM_KEYS = {key for key, _, _ in SYSTEM_PAGES}

ALL_PAGES = PAGES + SYSTEM_PAGES
ALL_KEYS = PAGE_KEYS | SYSTEM_KEYS


def allowed_pages(user: User | None) -> set[str]:
    if user is None or user.is_admin:
        return set(ALL_KEYS)
    if user.role is None:
        return set(PAGE_KEYS)
    return ALL_KEYS & set(user.role.pages or [])


def first_allowed_path(user: User | None) -> str | None:
    perms = allowed_pages(user)
    for key, _, path in ALL_PAGES:
        if key in perms:
            return path
    return None


def require_page(key: str):
    if key not in ALL_KEYS:  # опечатка в ключе — ошибка программиста
        raise ValueError(f"неизвестная вкладка: {key}")

    def dep(user: User = Depends(require_user)) -> User:
        if key not in allowed_pages(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="раздел недоступен для вашей роли",
            )
        return user

    return dep
