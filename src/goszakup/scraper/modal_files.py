"""Развёртывание документов за кнопкой «Перейти» (`actionModalShowFiles`).

На странице объявления тех.спецификация и конкурсная документация лежат не
прямой ссылкой, а за JS-кнопкой `actionModalShowFiles(anno_id, file_type_id)`.
Эта кнопка дёргает ajax-эндпоинт:

    GET https://goszakup.gov.kz/ru/announce/actionAjaxModalShowFiles/{anno}/{type}

В ответ — HTML-фрагмент `<table class="table table-bordered">` с прямыми
ссылками на файлы на хосте `v3bl.goszakup.gov.kz`. Авторизация не требуется
(проверено).

Что НЕ нужно качать: ссылку из колонки «Подпись» — это ЭЦП документа
(`/files/signature/download_cms/...`), нам она не нужна.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from ..config import ANNOUNCE_URL, BASE_URL
from .http import ThrottledSession

log = logging.getLogger(__name__)

_MODAL_URL = f"{BASE_URL}/ru/announce/actionAjaxModalShowFiles"


@dataclass(frozen=True)
class ModalFile:
    url: str
    name: str
    lot_number: str


# Эвристика «строка похожа на ТЗ» — используется И в scraper'е для решения
# «разворачивать ли modal у этого пункта», И в classify/llm.pick_tz_document
# для финального выбора файла. Намеренно в одном месте.
#
# Покрывает: «Техническая спецификация», «Конкурсная документация», «ТЗ»,
# «Описание предмета», «Техническая часть»; имена файлов вида `techspec_*`.
_TZ_LIKE_RE = re.compile(
    r"техническ\w*\s*специф"
    r"|конкурсн\w*\s*документац"
    r"|тех(ническое)?\s*задани"
    r"|\bтз\b"
    r"|описание\s+предмета"
    r"|техническая\s+часть"
    r"|\btechspec_",
    re.IGNORECASE,
)


def is_tz_like_name(text: str | None) -> bool:
    if not text:
        return False
    return bool(_TZ_LIKE_RE.search(text))


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _looks_like_signature(href: str) -> bool:
    # Ссылка вида /ru/files/signature/download_cms/... — это ЭЦП, не сам файл.
    return "/files/signature/" in href


def fetch_modal_files(
    announcement_id: int, file_type_id: int, *, session: ThrottledSession
) -> list[ModalFile]:
    """Возвращает реальные файлы, скрытые за «Перейти». Никогда не бросает."""
    url = f"{_MODAL_URL}/{announcement_id}/{file_type_id}"
    body = _try_fetch(url, announcement_id, session)
    if not body:
        return []
    files = _parse_table(body)
    if not files:
        log.warning(
            "modal %s/%s: ответ без файлов в таблице", announcement_id, file_type_id
        )
    return files


def _try_fetch(url: str, announcement_id: int, session: ThrottledSession) -> str | None:
    """Цепочка попыток с нарастающей «легендой» (XRW, прогрев cookie).

    Эмпирически голый GET анонимно отдаёт 200 — но цепочка нужна на случай,
    если goszakup затянет проверку (исторически анонимные ajax уходили в
    302 → /system_error/not_found, см. CLAUDE.md).
    """
    # 1) Голый GET без редиректов — самый дешёвый путь.
    try:
        r = session.get(url, allow_redirects=False)
    except Exception as e:
        log.warning("modal %s: request failed: %s", url, e)
        return None
    if _looks_ok(r):
        log.debug("modal %s: ok via plain GET", url)
        return r.text

    if r.status_code in (301, 302) and "system_error" in (r.headers.get("Location") or ""):
        log.debug("modal %s: plain GET redirected to system_error, escalating", url)

    # 2) С XHR-заголовками и Referer на страницу объявления.
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{ANNOUNCE_URL}/{announcement_id}?tab=documents",
    }
    try:
        r = session.get(url, headers=headers, allow_redirects=False)
    except Exception as e:
        log.warning("modal %s: XHR request failed: %s", url, e)
        return None
    if _looks_ok(r):
        log.info("modal %s: ok via XHR headers (fallback 2)", url)
        return r.text

    # 3) Прогреть cookie визитом на announcement и повторить XHR.
    try:
        session.get(f"{ANNOUNCE_URL}/{announcement_id}", params={"tab": "documents"})
        r = session.get(url, headers=headers, allow_redirects=False)
    except Exception as e:
        log.warning("modal %s: warmup+XHR failed: %s", url, e)
        return None
    if _looks_ok(r):
        log.info("modal %s: ok via warmup + XHR (fallback 3)", url)
        return r.text

    log.warning(
        "modal %s: все попытки провалены (status=%s, location=%s)",
        url,
        r.status_code,
        r.headers.get("Location", "-"),
    )
    return None


def _looks_ok(r) -> bool:
    return r.status_code == 200 and "table-bordered" in r.text


def _parse_table(html: str) -> list[ModalFile]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.table.table-bordered") or soup.find("table")
    if not table:
        return []
    # Парсинг по заголовкам колонок — НЕ по позиции (правило CLAUDE.md).
    headers = [_strip(th.get_text(" ")) for th in table.find_all("th")]
    idx = {h: i for i, h in enumerate(headers)}
    doc_col = idx.get("Документ")
    lot_col = idx.get("Номер лота")
    if doc_col is None:
        log.warning("modal: нет колонки «Документ» в таблице (заголовки: %s)", headers)
        return []

    out: list[ModalFile] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if not cells or doc_col >= len(cells):
            continue
        doc_cell = cells[doc_col]
        link = None
        for a in doc_cell.find_all("a", href=True):
            href = a["href"]
            if _looks_like_signature(href):
                continue
            link = a
            break
        if link is None:
            continue
        url_raw = link["href"]  # как есть — без нормализации (v3bl-странности)
        name = _strip(link.get_text(" ")) or url_raw.rsplit("/", 1)[-1]
        lot_number = (
            _strip(cells[lot_col].get_text(" "))
            if lot_col is not None and lot_col < len(cells)
            else ""
        )
        out.append(ModalFile(url=url_raw, name=name, lot_number=lot_number))
    return out
