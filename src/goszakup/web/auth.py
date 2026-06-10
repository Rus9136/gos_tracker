"""Аутентификация по таблице users + cookie-сессия.

Уходим от единой Basic-Auth-учётки из env к multi-tenant: пароли хранятся
bcrypt-хешами в БД, вход — форма /login, идентичность — в подписанной
cookie-сессии (`request.session["uid"]`).

bcrypt вызываем напрямую: passlib 1.7.4 несовместим с bcrypt 5.x. bcrypt
ограничивает пароль 72 байтами — обрезаем явно, иначе hashpw кидает
ValueError на длинных паролях.
"""

from __future__ import annotations

import os

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import User
from .deps import get_db

# GZ_NO_AUTH=1 — только для dev-машины: пропускает вход и работает под
# синтетическим админом без scope. На проде не ставится.
_AUTH_DISABLED = os.environ.get("GZ_NO_AUTH") == "1"


def _truncate(password: str) -> bytes:
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_truncate(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


class NotAuthenticated(Exception):
    """Нет валидной сессии. Ловится exception-handler'ом → редирект на /login."""

    def __init__(self, next_url: str = "/"):
        self.next_url = next_url


def _dev_admin() -> User:
    """Синтетический админ для GZ_NO_AUTH — в БД не пишется, scope пустой."""
    return User(id=0, username="anon", password_hash="", is_admin=True, is_active=True)


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_current_user(request: Request, db: Session) -> User | None:
    if _AUTH_DISABLED:
        return _dev_admin()
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, uid)
    if user is None or not user.is_active:
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if user is None:
        raise NotAuthenticated(next_url=request.url.path)
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return user


def seed_admin_from_env(db: Session) -> None:
    """Плавная миграция прода: если users пуста и заданы GZ_USER/GZ_PASSWORD —
    создаём из них первого админа. После этого env-переменные становятся
    только сидом; реальный вход идёт по БД."""
    if db.scalar(select(func.count(User.id))):
        return
    username = os.environ.get("GZ_USER")
    password = os.environ.get("GZ_PASSWORD")
    if not username or not password:
        return
    db.add(
        User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
            is_active=True,
        )
    )
    db.commit()
