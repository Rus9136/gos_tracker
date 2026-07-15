"""verify-7: секреты автоподачи не утекают в Sentry.

Sentry с send_default_pii=True сериализует repr() локальных переменных в стеке
при исключении. repr() dataclass'ов с секретами клиента раскрыл бы p12/пароль/
PIN/цену. Здесь: repr не показывает секреты + EventScrubber чистит их по ключам.
"""

from __future__ import annotations

from goszakup import observability
from goszakup.autosubmit.rpc import LotBid, RunRequest
from goszakup.vault.credentials import DecryptedCredential


def test_run_request_repr_hides_secrets():
    r = RunRequest(
        submission_id=1,
        anno_id=2,
        open_at_iso="2026-07-15T10:00:00+00:00",
        lot_bids=[],
        p12_b64="SECRETP12",
        portal_password="SECRETPW",
        key_pin="4321",
    )
    s = repr(r)
    for secret in ("SECRETP12", "SECRETPW", "4321"):
        assert secret not in s
    assert "submission_id=1" in s  # несекретные поля видны


def test_lotbid_repr_hides_price():
    assert "999999" not in repr(LotBid(lot_id=1, price="999999"))


def test_decrypted_credential_repr_hides_secrets():
    c = DecryptedCredential(p12_bytes=b"SECRETBYTES", portal_password="SECRETPW", key_pin="4321")
    s = repr(c)
    assert "SECRETBYTES" not in s
    assert "SECRETPW" not in s
    assert "4321" not in s


def test_event_scrubber_removes_secret_fields():
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber

    scrubber = EventScrubber(
        denylist=DEFAULT_DENYLIST + observability.SECRET_DENYLIST_EXTRA, recursive=True
    )
    event = {
        "extra": {"portal_password": "SECRETPW", "price": "100500", "harmless": "ok"},
    }
    scrubber.scrub_event(event)
    assert event["extra"]["portal_password"] != "SECRETPW"
    assert event["extra"]["price"] != "100500"
    assert event["extra"]["harmless"] == "ok"
