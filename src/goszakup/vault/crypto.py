"""AES-256-GCM шифрование секретов клиента (.p12, пароль портала, PIN).

Почему GCM, а не fernet/CBC: встроенный auth-tag даёт целостность — подмена
шифртекста в БД даёт `InvalidTag` при расшифровке, а не «тихо неверные» байты,
которыми потом подпишется мусор. Каждый секрет шифруется своим 12-байтным
nonce (хранится рядом с шифртекстом); один и тот же plaintext даёт разный
шифртекст — это норма и защита от корреляции.

Мастер-ключ берётся лениво (`_master_key()`), а не при импорте: модуль должен
импортироваться и на машинах без vault (dev/CI), падать только при реальном
обращении к шифрованию. В проде ключ обязан приходить из KMS/HSM — здесь это
заглушка через env (`GZ_VAULT_MASTER_KEY`, base64 от 32 байт).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LEN = 12


def _master_key() -> bytes:
    raw = os.environ.get("GZ_VAULT_MASTER_KEY")
    if not raw:
        raise RuntimeError(
            "GZ_VAULT_MASTER_KEY не задан — KeyVault недоступен. "
            "Сгенерировать: python -c \"import os,base64;"
            "print(base64.b64encode(os.urandom(32)).decode())\""
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise ValueError("GZ_VAULT_MASTER_KEY должен быть base64 от ровно 32 байт (AES-256)")
    return key


def encrypt(plaintext: bytes) -> tuple[str, str]:
    """Зашифровать → (data_b64, nonce_b64). Tag упакован внутрь data."""
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(_master_key()).encrypt(nonce, plaintext, None)
    return base64.b64encode(ct).decode(), base64.b64encode(nonce).decode()


def decrypt(data_b64: str, nonce_b64: str) -> bytes:
    """Расшифровать (data_b64, nonce_b64) → plaintext. InvalidTag при подмене."""
    ct = base64.b64decode(data_b64)
    nonce = base64.b64decode(nonce_b64)
    return AESGCM(_master_key()).decrypt(nonce, ct, None)


def encrypt_str(plaintext: str) -> tuple[str, str]:
    return encrypt(plaintext.encode("utf-8"))


def decrypt_str(data_b64: str, nonce_b64: str) -> str:
    return decrypt(data_b64, nonce_b64).decode("utf-8")
