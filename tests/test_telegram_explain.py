"""Кнопка «Подробнее» в Telegram-уведомлении.

Проверяем:
- вебхук выключен без секрета, отвергает чужой секрет, на валидный callback
  ставит explain_actor и гасит «часики»;
- explain_actor отвечает только известным chat_id, шлёт объяснение и
  fallback при ошибке LLM;
- render: клавиатура с callback_data и экранирование в сообщении-объяснении.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from goszakup.db.models import Announcement, Lot, User


@pytest.fixture
def seeded(db_session):
    db_session.execute(User.__table__.delete())
    user = User(
        username="tg", password_hash="",
        telegram_chat_id="123456", notify_telegram=True,
    )
    db_session.add(user)
    db_session.add(Announcement(id=101, url="https://goszakup.gov.kz/ru/announce/index/101"))
    lot = Lot(
        id=1, url="https://goszakup.gov.kz/ru/announce/index/101",
        announcement_id=101, name="Доработка 1С <ERP>", plan_amount=5_000_000,
        kato="751000000",
    )
    db_session.add(lot)
    db_session.commit()
    return user, lot


# === render ===

def test_explain_keyboard_has_callback(seeded):
    from goszakup.notify.render import build_explain_keyboard

    _user, lot = seeded
    kb = build_explain_keyboard(lot)
    assert kb["inline_keyboard"][0][0]["callback_data"] == "explain:1"


def test_explain_message_escapes(seeded):
    from goszakup.notify.render import build_explain_message

    _user, lot = seeded
    msg = build_explain_message(lot, "Нужно доработать <конфигурацию> 1С.")
    assert "&lt;конфигурацию&gt;" in msg
    assert "&lt;ERP&gt;" in msg
    assert "/lot/1" in msg


# === webhook ===

def _cb_update(data: str = "explain:1", chat_id: int = 123456) -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cbq-1",
            "data": data,
            "message": {"chat": {"id": chat_id}},
        },
    }


@pytest.fixture
def client(monkeypatch):
    import goszakup.web.app as app_mod

    monkeypatch.setattr(app_mod, "TELEGRAM_WEBHOOK_SECRET", "s3cret")
    with TestClient(app_mod.app) as c:
        yield c


def test_webhook_disabled_without_secret(monkeypatch):
    import goszakup.web.app as app_mod

    monkeypatch.setattr(app_mod, "TELEGRAM_WEBHOOK_SECRET", None)
    with TestClient(app_mod.app) as c:
        r = c.post("/telegram/webhook", json=_cb_update())
    assert r.status_code == 503


def test_webhook_rejects_wrong_secret(client):
    r = client.post(
        "/telegram/webhook",
        json=_cb_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 401


def test_webhook_enqueues_explain(client, monkeypatch):
    import goszakup.notify.telegram as tg
    import goszakup.queue.notify as notify_mod

    enq: list[tuple] = []
    answered: list[str] = []
    monkeypatch.setattr(notify_mod.explain_actor, "send", lambda *a: enq.append(a))
    monkeypatch.setattr(tg, "answer_callback_query", lambda cid, text=None: answered.append(cid) or (True, None))
    monkeypatch.setattr(tg, "send_placeholder", lambda chat_id, text: 777)

    r = client.post(
        "/telegram/webhook",
        json=_cb_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200
    assert enq == [(1, "123456", 777)]
    assert answered == ["cbq-1"]


def test_webhook_ignores_other_updates(client, monkeypatch):
    import goszakup.queue.notify as notify_mod

    enq: list[tuple] = []
    monkeypatch.setattr(notify_mod.explain_actor, "send", lambda *a: enq.append(a))

    r = client.post(
        "/telegram/webhook",
        json={"update_id": 2, "message": {"text": "привет"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert r.status_code == 200
    assert enq == []


# === explain_actor ===

def test_explain_actor_sends_explanation(seeded, db_session, monkeypatch):
    import goszakup.classify.llm as llm_mod
    import goszakup.queue.notify as notify_mod

    sent: list[str] = []
    monkeypatch.setattr(llm_mod, "explain_lot", lambda lot: ("Простое объяснение.", None))
    monkeypatch.setattr(
        notify_mod, "send_message",
        lambda chat_id, text, **kw: (sent.append(text) or (True, None)),
    )

    notify_mod.explain_actor(1, "123456")
    assert len(sent) == 1
    assert "Простое объяснение." in sent[0]
    assert "Простыми словами" in sent[0]


def test_explain_actor_edits_placeholder(seeded, db_session, monkeypatch):
    import goszakup.classify.llm as llm_mod
    import goszakup.queue.notify as notify_mod

    edited: list[tuple] = []
    sent: list[str] = []
    monkeypatch.setattr(llm_mod, "explain_lot", lambda lot: ("Простое объяснение.", None))
    monkeypatch.setattr(
        notify_mod, "edit_message",
        lambda chat_id, mid, text, **kw: (edited.append((mid, text)) or (True, None)),
    )
    monkeypatch.setattr(
        notify_mod, "send_message",
        lambda chat_id, text, **kw: (sent.append(text) or (True, None)),
    )

    notify_mod.explain_actor(1, "123456", 777)
    # Ответ отредактирован в заглушку, новое сообщение не отправлялось.
    assert len(edited) == 1 and edited[0][0] == 777
    assert "Простое объяснение." in edited[0][1]
    assert sent == []


def test_explain_actor_falls_back_when_edit_fails(seeded, db_session, monkeypatch):
    import goszakup.classify.llm as llm_mod
    import goszakup.queue.notify as notify_mod

    sent: list[str] = []
    monkeypatch.setattr(llm_mod, "explain_lot", lambda lot: ("Простое объяснение.", None))
    monkeypatch.setattr(
        notify_mod, "edit_message",
        lambda chat_id, mid, text, **kw: (False, "message to edit not found"),
    )
    monkeypatch.setattr(
        notify_mod, "send_message",
        lambda chat_id, text, **kw: (sent.append(text) or (True, None)),
    )

    notify_mod.explain_actor(1, "123456", 777)
    assert len(sent) == 1
    assert "Простое объяснение." in sent[0]


def test_explain_actor_unknown_chat_silent(seeded, db_session, monkeypatch):
    import goszakup.queue.notify as notify_mod

    sent: list[str] = []
    monkeypatch.setattr(
        notify_mod, "send_message",
        lambda chat_id, text, **kw: (sent.append(text) or (True, None)),
    )

    notify_mod.explain_actor(1, "999999")
    assert sent == []


def test_explain_actor_llm_error_fallback(seeded, db_session, monkeypatch):
    import goszakup.classify.llm as llm_mod
    import goszakup.queue.notify as notify_mod

    def boom(lot):
        raise RuntimeError("CEREBRAS_API_KEY не задан")

    sent: list[str] = []
    monkeypatch.setattr(llm_mod, "explain_lot", boom)
    monkeypatch.setattr(
        notify_mod, "send_message",
        lambda chat_id, text, **kw: (sent.append(text) or (True, None)),
    )

    notify_mod.explain_actor(1, "123456")
    assert len(sent) == 1
    assert "Не получилось" in sent[0]
