"""Создание и расшифровка учёток клиента (ClientCredential) поверх crypto.py.

Шифрование — на записи (`create_credential`), расшифровка — точечно перед
использованием (`decrypt_credential`), результат живёт только в RAM вызывающего
кода (на Windows submit-agent) и НЕ логируется (гайд §8.3).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..db.models import ClientCredential
from . import crypto


@dataclass(frozen=True)
class DecryptedCredential:
    """Секреты клиента в открытом виде — только на время использования."""

    p12_bytes: bytes
    portal_password: str
    key_pin: str | None


def create_credential(
    session: Session,
    *,
    label: str,
    p12_bytes: bytes,
    portal_password: str,
    iin_bin: str | None = None,
    key_pin: str | None = None,
    notes: str | None = None,
) -> ClientCredential:
    """Зашифровать секреты и создать строку ClientCredential (без commit)."""
    p12_d, p12_n = crypto.encrypt(p12_bytes)
    pw_d, pw_n = crypto.encrypt_str(portal_password)
    pin_d, pin_n = (crypto.encrypt_str(key_pin) if key_pin else (None, None))

    cred = ClientCredential(
        label=label,
        iin_bin=iin_bin,
        p12_enc=p12_d,
        p12_nonce=p12_n,
        portal_password_enc=pw_d,
        portal_password_nonce=pw_n,
        key_pin_enc=pin_d,
        key_pin_nonce=pin_n,
        notes=notes,
    )
    session.add(cred)
    return cred


def decrypt_credential(cred: ClientCredential) -> DecryptedCredential:
    """Расшифровать секреты учётки для немедленного использования."""
    key_pin = (
        crypto.decrypt_str(cred.key_pin_enc, cred.key_pin_nonce)
        if cred.key_pin_enc and cred.key_pin_nonce
        else None
    )
    return DecryptedCredential(
        p12_bytes=crypto.decrypt(cred.p12_enc, cred.p12_nonce),
        portal_password=crypto.decrypt_str(
            cred.portal_password_enc, cred.portal_password_nonce
        ),
        key_pin=key_pin,
    )
