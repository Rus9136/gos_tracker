"""Простая HTTP Basic auth с одной учётной записью из env."""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


def _expected() -> tuple[str, str]:
    user = os.environ.get("GZ_USER", "admin")
    password = os.environ.get("GZ_PASSWORD", "admin")
    return user, password


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    user, password = _expected()
    ok_user = secrets.compare_digest(credentials.username, user)
    ok_pwd = secrets.compare_digest(credentials.password, password)
    if not (ok_user and ok_pwd):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
