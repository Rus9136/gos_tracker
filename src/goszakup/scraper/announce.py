"""Парсер детальной страницы объявления (5 табов).

Возвращает датакласс AnnouncementDetail с заголовками, лотами, документами,
контрактами. Не пишет в БД — это делает пайплайн.

Замечание про документы: «Перейти» — это JS-кнопка, дёргающая ajax
(actionModalShowFiles), который без авторизации отдаёт 404. Поэтому
сохраняем строку (name, attribute, file_type_id), а скачиваем только те,
у которых на странице есть прямая <a href>.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape

from bs4 import BeautifulSoup, Tag

from ..config import ANNOUNCE_URL, SUBPRICEOFFER_URL
from .http import ThrottledSession

log = logging.getLogger(__name__)


def _clean(s: str | None) -> str:
    if not s:
        return ""
    return unescape(re.sub(r"\s+", " ", s)).strip()


def _parse_amount(s: str) -> float | None:
    if not s:
        return None
    s = s.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(s: str) -> datetime | None:
    s = _clean(s)
    # ISO (`2026-06-03 15:55:00`) — формат полей-инпутов на табе general;
    # dd.mm.yyyy — формат старых табличных представлений.
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# С 2024-03-01 весь Казахстан в едином поясе UTC+5 (Asia/Almaty). Время на
# goszakup — местное, поэтому дедлайн приёма заявок локализуем как +05:00 и
# приводим к UTC: иначе сравнение `application_end < now()` (UTC) промахнётся
# на 5 часов и лот провисит «актуальным» лишние полдня.
_ALMATY_TZ = timezone(timedelta(hours=5))


def _parse_deadline(s: str) -> datetime | None:
    d = _parse_date(s)
    if d is None:
        return None
    return d.replace(tzinfo=_ALMATY_TZ).astimezone(timezone.utc)


def _find_deadline(kv: dict[str, str]) -> datetime | None:
    """Ищет в kv-таблице «Общих сведений» строку окончания приёма заявок.

    Точное название поля у goszakup плавает по способу закупки («Дата и время
    окончания приёма заявок», «Срок окончания приёма заявок», для аукциона —
    «…приёма ценовых предложений»), поэтому матчим по подстроке, а не по
    фиксированному ключу.
    """
    for key, value in kv.items():
        low = key.lower().replace("ё", "е")
        if "окончан" in low and ("заяв" in low or "ценов" in low):
            d = _parse_deadline(value)
            if d:
                return d
    return None


@dataclass
class LotDetail:
    number: str
    customer_bin: str
    customer_name: str
    enstru: str
    name: str
    extra: str
    price_per_unit: float | None
    quantity: float | None
    unit: str
    plan_amount: float | None
    amount_y1: float | None
    amount_y2: float | None
    amount_y3: float | None
    status_name: str
    # Цифровой «Код ТРУ». HTML-парсер его не знает (он живёт на отдельной
    # карточке subpriceoffer, см. fetch_lot_enstru_code); API-источник
    # заполняет сразу — экономит +1 запрос на IT-лот.
    enstru_code: str | None = None


@dataclass
class DocumentRow:
    name: str
    attribute: str  # «Да»/«Нет»
    file_type_id: int | None  # параметр для actionModalShowFiles(anno, type)
    direct_url: str | None  # прямая ссылка <a href>, если есть


@dataclass
class ContractRow:
    lot_number: str  # привязка к лоту в шапке группы
    contract_number: str
    status: str
    plan_amount: float | None
    contract_amount: float | None
    fact_amount: float | None
    supplier_name: str
    supplier_bin: str
    supplier_status: str


@dataclass
class WinnerRow:
    """Строка вкладки «Информация о победителях» (tab=winners).

    Цены победителя там нет — только плановая сумма; фактическая сумма
    берётся из договора (tab=contracts), когда он появится.
    """

    lot_number: str
    lot_status: str
    plan_amount: float | None
    winner_bin: str
    winner_name: str
    second_bin: str
    second_name: str


@dataclass
class AnnouncementDetail:
    id: int
    url: str
    number: str = ""
    method: str = ""
    purchase_type: str = ""
    subject_type: str = ""
    organizer_bin: str = ""
    organizer_name: str = ""
    organizer_address: str = ""
    lots_count: int | None = None
    total_amount: float | None = None
    attributes: str = ""
    publish_date: datetime | None = None
    application_end: datetime | None = None  # окончание приёма заявок, UTC
    contact_name: str = ""
    contact_role: str = ""
    contact_email: str = ""
    lots: list[LotDetail] = field(default_factory=list)
    documents: list[DocumentRow] = field(default_factory=list)
    contracts: list[ContractRow] = field(default_factory=list)
    winners: list[WinnerRow] = field(default_factory=list)


def _split_bin_name(s: str) -> tuple[str, str]:
    """'010940002046 Государственное коммунальное предприятие ...' → (bin, name)."""
    s = _clean(s)
    m = re.match(r"^(\d{6,20})\s+(.+)$", s)
    if m:
        return m.group(1), m.group(2)
    return "", s


def _kv_table_rows(table: Tag) -> dict[str, str]:
    """Парсит таблицы вида [<th>label</th><td>value</td>] и возвращает dict label→value."""
    result: dict[str, str] = {}
    for tr in table.find_all("tr", recursive=False) or table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = _clean(cells[0].get_text(" "))
        value = _clean(cells[1].get_text(" "))
        if label:
            result[label.rstrip(":")] = value
    return result


def _form_pairs(soup: BeautifulSoup) -> dict[str, str]:
    """Карточка-шапка объявления — это форма `<label>…</label> <input value=…>`,
    а не таблица. Значения (даты публикации/начала/окончания приёма) лежат в
    атрибуте `value` input'а, поэтому `get_text()` их не видит. Возвращает
    dict label→value по каждому label, у которого в том же form-group есть input.
    """
    result: dict[str, str] = {}
    for lab in soup.find_all("label"):
        label = _clean(lab.get_text(" "))
        if not label:
            continue
        grp = lab.parent
        inp = grp.find("input") if grp else None
        if inp is not None and inp.get("value") is not None:
            result.setdefault(label.rstrip(":"), _clean(inp.get("value")))
    return result


def _parse_general(soup: BeautifulSoup, detail: AnnouncementDetail) -> None:
    # Срок окончания/начала приёма и дата публикации — в форме-шапке (input value).
    form = _form_pairs(soup)
    if form:
        detail.application_end = _find_deadline(form)
        for date_key in ("Дата публикации объявления", "Дата публикации"):
            if date_key in form:
                d = _parse_date(form[date_key])
                if d:
                    detail.publish_date = d
                    break

    tables = soup.select("table")
    # Первая kv-таблица — общие сведения. Вторая — представители.
    for t in tables[:4]:
        kv = _kv_table_rows(t)
        if not kv:
            continue
        if "Способ проведения закупки" in kv:
            detail.method = kv.get("Способ проведения закупки", "")
            detail.purchase_type = kv.get("Тип закупки", "")
            detail.subject_type = kv.get("Вид предмета закупок", "")
            detail.organizer_bin, detail.organizer_name = _split_bin_name(
                kv.get("Организатор", "")
            )
            detail.organizer_address = kv.get("Юр. адрес организатора", "")
            lc = kv.get("Кол-во лотов в объявлении", "")
            detail.lots_count = int(lc) if lc.isdigit() else None
            detail.total_amount = _parse_amount(kv.get("Сумма закупки", ""))
            detail.attributes = kv.get("Признаки", "")
            if detail.publish_date is None:
                for date_key in ("Дата публикации", "Дата начала приема заявок"):
                    if date_key in kv:
                        d = _parse_date(kv[date_key])
                        if d:
                            detail.publish_date = d
                            break
            # не затираем значение, уже взятое из формы-шапки
            detail.application_end = detail.application_end or _find_deadline(kv)
        elif "ФИО представителя" in kv:
            detail.contact_name = kv.get("ФИО представителя", "")
            detail.contact_role = kv.get("Должность", "")
            detail.contact_email = kv.get("E-Mail", "") or kv.get("Email", "")


def _parse_lots(soup: BeautifulSoup, detail: AnnouncementDetail) -> None:
    table = None
    for t in soup.select("table"):
        hdrs = [_clean(th.get_text(" ")) for th in t.find_all("th")]
        if hdrs and "Номер лота" in hdrs and "Плановая сумма" in hdrs:
            table = t
            break
    if not table:
        return
    headers = [_clean(th.get_text(" ")) for th in table.find_all("th")]
    idx = {h: i for i, h in enumerate(headers)}

    def cell(row, key: str) -> str:
        i = idx.get(key)
        if i is None:
            return ""
        cells = row.find_all("td")
        if i >= len(cells):
            return ""
        return _clean(cells[i].get_text(" "))

    for tr in table.find_all("tr"):
        if not tr.find("td"):
            continue
        bin_, cust_name = _split_bin_name(cell(tr, "Заказчик"))
        # qty может содержать «1» как число
        qty_raw = cell(tr, "Кол-во")
        try:
            qty = float(qty_raw.replace(" ", "").replace(",", ".")) if qty_raw else None
        except ValueError:
            qty = None
        detail.lots.append(
            LotDetail(
                number=cell(tr, "Номер лота"),
                customer_bin=bin_,
                customer_name=cust_name,
                enstru=cell(tr, "Наименование"),
                name=cell(tr, "Наименование"),
                extra=cell(tr, "Дополнительная характеристика"),
                price_per_unit=_parse_amount(cell(tr, "Цена за ед.")),
                quantity=qty,
                unit=cell(tr, "Ед. изм."),
                plan_amount=_parse_amount(cell(tr, "Плановая сумма")),
                amount_y1=_parse_amount(cell(tr, "Сумма 1 год")),
                amount_y2=_parse_amount(cell(tr, "Сумма 2 год")),
                amount_y3=_parse_amount(cell(tr, "Сумма 3 год")),
                status_name=cell(tr, "Статус лота"),
            )
        )


_RE_FILE_TYPE = re.compile(r"actionModalShowFiles\(\d+\s*,\s*(\d+)\)")


def _parse_documents(soup: BeautifulSoup, detail: AnnouncementDetail) -> None:
    for table in soup.select("table"):
        hdrs = [_clean(th.get_text(" ")) for th in table.find_all("th")]
        if not (hdrs and "Наименование документа" in hdrs and "Признак" in hdrs):
            continue
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            name = _clean(tds[0].get_text(" "))
            attr = _clean(tds[1].get_text(" "))
            # last cell: либо <a href=...>, либо <button onclick=actionModalShowFiles(...)>
            file_type_id: int | None = None
            direct: str | None = None
            if len(tds) >= 3:
                last = tds[2]
                btn = last.find("button")
                if btn and btn.get("onclick"):
                    m = _RE_FILE_TYPE.search(btn["onclick"])
                    if m:
                        file_type_id = int(m.group(1))
            link = tds[0].find("a", href=True) or (
                tds[2].find("a", href=True) if len(tds) >= 3 else None
            )
            if link:
                direct = link["href"]
            detail.documents.append(
                DocumentRow(
                    name=name,
                    attribute=attr,
                    file_type_id=file_type_id,
                    direct_url=direct,
                )
            )
        return


def _parse_contracts(soup: BeautifulSoup, detail: AnnouncementDetail) -> None:
    for table in soup.select("table"):
        hdrs_all = [_clean(th.get_text(" ")) for th in table.find_all("th")]
        if not hdrs_all or "Номер договора" not in hdrs_all:
            continue
        current_lot = ""
        column_headers: list[str] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            txts = [_clean(c.get_text(" ")) for c in cells]
            if not txts:
                continue
            # Заголовок группы (одна объединённая ячейка с «Лот №...»)
            if (
                len(cells) == 1
                and cells[0].name == "th"
                and "Лот" in txts[0]
            ):
                current_lot = txts[0]
                # вытащим только номер лота
                m = re.search(r"Лот\s*№([\w\-]+)", current_lot)
                current_lot = m.group(1) if m else current_lot
                continue
            # Шапка колонок договоров
            if cells[0].name == "th" and "Номер договора" in txts:
                column_headers = txts
                continue
            # Информационная строка «отсутствует»
            if len(cells) == 1 and "отсутствует" in txts[0].lower():
                continue
            # Строка с данными
            if column_headers and cells[0].name == "td":
                row = dict(zip(column_headers, txts, strict=False))
                detail.contracts.append(
                    ContractRow(
                        lot_number=current_lot,
                        contract_number=row.get("Номер договора", ""),
                        status=row.get("Статус договора", ""),
                        plan_amount=_parse_amount(row.get("Плановая сумма лота, тенге", "")),
                        contract_amount=_parse_amount(
                            row.get(
                                "Сумма по предмету договора, тенге (Без учета НДС)",
                                "",
                            )
                        ),
                        fact_amount=_parse_amount(
                            row.get(
                                "Сумма предмета договора (лот) исполненная, фактическая, тенге (Без учета НДС)",
                                "",
                            )
                        ),
                        supplier_name=row.get("Наименование поставщика", ""),
                        supplier_bin=row.get("БИН/ИИН поставщика", ""),
                        supplier_status=row.get("Статус победителя", ""),
                    )
                )


def _parse_winners(soup: BeautifulSoup, detail: AnnouncementDetail) -> None:
    for table in soup.select("table"):
        headers = [_clean(th.get_text(" ")) for th in table.find_all("th")]
        if not headers or "Победитель" not in headers or "Номер лота" not in headers:
            continue
        idx = {h: i for i, h in enumerate(headers)}

        def cell(row, key: str) -> str:
            i = idx.get(key)
            if i is None:
                return ""
            cells = row.find_all("td")
            if i >= len(cells):
                return ""
            return _clean(cells[i].get_text(" "))

        for tr in table.find_all("tr"):
            if not tr.find("td"):
                continue
            winner_bin, winner_name = _split_bin_name(cell(tr, "Победитель"))
            second_bin, second_name = _split_bin_name(
                cell(tr, "Поставщик, занявший второе место")
            )
            if not (winner_bin or winner_name):
                continue
            detail.winners.append(
                WinnerRow(
                    lot_number=cell(tr, "Номер лота"),
                    lot_status=cell(tr, "Статус лота"),
                    plan_amount=_parse_amount(cell(tr, "Плановая сумма лота, тенге")),
                    winner_bin=winner_bin,
                    winner_name=winner_name,
                    second_bin=second_bin,
                    second_name=second_name,
                )
            )
        return


def _winners_possible(detail: AnnouncementDetail) -> bool:
    """Пока все лоты в «открытых» статусах, победителей нет по определению —
    экономим запрос tab=winners. Detail-фаза повторяется на смене статуса,
    так что вкладку дёрнем, как только лот уйдёт из приёма заявок."""
    from .statuses import ACTUAL_STATUSES, STATUS_NAMES

    open_names = {STATUS_NAMES[c] for c in ACTUAL_STATUSES}
    if not detail.lots:
        return True
    return any(ld.status_name and ld.status_name not in open_names for ld in detail.lots)


def fetch_announcement(
    anno_id: int, session: ThrottledSession | None = None
) -> AnnouncementDetail:
    sess = session or ThrottledSession()
    detail = AnnouncementDetail(id=anno_id, url=f"{ANNOUNCE_URL}/{anno_id}")

    # general
    r = sess.get(f"{ANNOUNCE_URL}/{anno_id}", params={"tab": "general"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    h3 = soup.find("h3")
    if h3:
        m = re.search(r"№\s*([\w\-]+)", h3.get_text(" "))
        if m:
            detail.number = m.group(1)
    _parse_general(soup, detail)

    # lots
    r = sess.get(f"{ANNOUNCE_URL}/{anno_id}", params={"tab": "lots"})
    r.raise_for_status()
    _parse_lots(BeautifulSoup(r.text, "lxml"), detail)

    # documents
    r = sess.get(f"{ANNOUNCE_URL}/{anno_id}", params={"tab": "documents"})
    r.raise_for_status()
    _parse_documents(BeautifulSoup(r.text, "lxml"), detail)

    # contracts (на ранних статусах часто пусто, но запрос дешёвый)
    r = sess.get(f"{ANNOUNCE_URL}/{anno_id}", params={"tab": "contracts"})
    r.raise_for_status()
    _parse_contracts(BeautifulSoup(r.text, "lxml"), detail)

    # winners: победитель появляется раньше договора (у ценовых предложений
    # договор может отсутствовать вовсе), поэтому это отдельный источник.
    if _winners_possible(detail):
        r = sess.get(f"{ANNOUNCE_URL}/{anno_id}", params={"tab": "winners"})
        r.raise_for_status()
        _parse_winners(BeautifulSoup(r.text, "lxml"), detail)

    return detail


def fetch_lot_enstru_code(
    trd_buy_id: int | str, lot_id: int, *, session: ThrottledSession | None = None
) -> str | None:
    """Цифровой «Код ТРУ» лота (напр. 192021.530.000001).

    Ни в листинге, ни в табах объявления его нет — он живёт только на карточке
    ценового предложения лота (`subpriceoffer`), строкой `<th>Код ТРУ</th><td>…`.
    Это отдельный +1 запрос на лот, поэтому вызывать точечно (см. правило про IT).
    `trd_buy_id` совпадает с `announcement_id`.
    """
    sess = session or ThrottledSession()
    r = sess.get(f"{SUBPRICEOFFER_URL}/{trd_buy_id}/{lot_id}")
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "lxml")
    th = soup.find("th", string=re.compile(r"Код\s+ТРУ"))
    if th is None:
        return None
    td = th.find_next("td")
    code = _clean(td.get_text()) if td else ""
    return code or None
