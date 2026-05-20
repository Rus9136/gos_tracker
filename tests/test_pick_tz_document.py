"""`pick_tz_document`: финальный выбор ТЗ-файла из приложений объявления.

Стабильно проверяемая, чистая логика — без LLM, БД и сети. Поэтому работаем
с дешёвыми SimpleNamespace-стабами вместо реальных ORM-объектов.
"""

from __future__ import annotations

from types import SimpleNamespace

from goszakup.classify.llm import pick_tz_document


def _doc(*, name="", attribute=None, local_path=None, sha256="x", size=1000):
    return SimpleNamespace(
        name=name,
        attribute=attribute,
        local_path=local_path,
        sha256=sha256,
        size=size,
    )


def test_returns_none_when_no_downloaded_files():
    # У документов нет local_path / sha256 — считаются не скачанными.
    docs = [_doc(name="Техническая спецификация", local_path=None, sha256=None)]
    assert pick_tz_document(docs) is None


def test_skips_non_supported_extensions():
    # .doc не поддерживается, должен быть отфильтрован.
    docs = [_doc(name="Техспец", local_path="/tmp/old.doc")]
    assert pick_tz_document(docs) is None


def test_picks_techspec_over_competition_doc():
    techspec = _doc(name="Техническая спецификация", local_path="/tmp/techspec.pdf")
    konkurs = _doc(name="Конкурсная документация", local_path="/tmp/konkurs.pdf")
    chosen = pick_tz_document([konkurs, techspec])
    assert chosen.name == "Техническая спецификация"


def test_prefers_pdf_over_docx_at_same_specificity():
    pdf = _doc(name="Техническая спецификация", local_path="/tmp/spec.pdf")
    docx = _doc(name="Техническая спецификация", local_path="/tmp/spec.docx")
    chosen = pick_tz_document([docx, pdf])
    assert chosen.local_path.endswith(".pdf")


def test_falls_back_to_any_downloaded_when_no_tz_like():
    # Ни одно имя не похоже на ТЗ — берём что есть из скачанного, чтобы LLM
    # хоть на что-то посмотрел (а не уходил в low-confidence без файла).
    docs = [_doc(name="Приложение 5", local_path="/tmp/x.pdf")]
    chosen = pick_tz_document(docs)
    assert chosen is not None
    assert chosen.local_path == "/tmp/x.pdf"
