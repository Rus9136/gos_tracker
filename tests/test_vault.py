"""P0-9: KeyVault — шифрование секретов клиента (crypto.py / credentials.py).

Vault хранит чужие .p12/пароль/PIN и подаёт заявки за реальные деньги, но не был
покрыт ни одним тестом. Проверяем round-trip, детект подмены шифртекста
(InvalidTag), неверный/отсутствующий мастер-ключ.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidTag

from goszakup.vault import crypto
from goszakup.vault.credentials import create_credential, decrypt_credential

_KEY_A = base64.b64encode(b"A" * 32).decode()
_KEY_B = base64.b64encode(b"B" * 32).decode()


@pytest.fixture
def master_key(monkeypatch):
    monkeypatch.setenv("GZ_VAULT_MASTER_KEY", _KEY_A)


def test_encrypt_decrypt_roundtrip(master_key):
    data = b"\x00binary p12 bytes\xff"
    ct, nonce = crypto.encrypt(data)
    assert crypto.decrypt(ct, nonce) == data


def test_encrypt_str_roundtrip(master_key):
    ct, nonce = crypto.encrypt_str("пароль-портала-😀")
    assert crypto.decrypt_str(ct, nonce) == "пароль-портала-😀"


def test_distinct_nonce_per_encrypt(master_key):
    ct1, n1 = crypto.encrypt_str("same")
    ct2, n2 = crypto.encrypt_str("same")
    assert n1 != n2 and ct1 != ct2  # рандомный nonce → разный шифртекст


def test_tampered_ciphertext_raises_invalid_tag(master_key):
    ct, nonce = crypto.encrypt_str("secret")
    raw = bytearray(base64.b64decode(ct))
    raw[0] ^= 0x01  # подмена одного байта
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(InvalidTag):
        crypto.decrypt_str(tampered, nonce)


def test_wrong_master_key_raises_invalid_tag(master_key, monkeypatch):
    ct, nonce = crypto.encrypt_str("secret")
    monkeypatch.setenv("GZ_VAULT_MASTER_KEY", _KEY_B)  # другой ключ
    with pytest.raises(InvalidTag):
        crypto.decrypt_str(ct, nonce)


def test_missing_master_key_raises(monkeypatch):
    monkeypatch.delenv("GZ_VAULT_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GZ_VAULT_MASTER_KEY"):
        crypto.encrypt_str("x")


def test_invalid_key_length_raises(monkeypatch):
    monkeypatch.setenv("GZ_VAULT_MASTER_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(ValueError, match="32 байт"):
        crypto.encrypt_str("x")


def test_credential_roundtrip(master_key, db_session):
    cred = create_credential(
        db_session,
        label="Клиент А",
        p12_bytes=b"\x30\x82 fake p12",
        portal_password="portal-pw",
        iin_bin="123456789012",
        key_pin="1234",
    )
    db_session.commit()

    # В БД — только шифртекст, не plaintext.
    assert cred.p12_enc and "portal-pw" not in cred.portal_password_enc

    dec = decrypt_credential(cred)
    assert dec.p12_bytes == b"\x30\x82 fake p12"
    assert dec.portal_password == "portal-pw"
    assert dec.key_pin == "1234"


def test_credential_without_pin(master_key, db_session):
    cred = create_credential(
        db_session,
        label="Без PIN",
        p12_bytes=b"p12",
        portal_password="pw",
        key_pin=None,
    )
    db_session.commit()
    dec = decrypt_credential(cred)
    assert dec.key_pin is None
