"""FastAPI приложение."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import __version__
from ..classify.llm import (
    DEV_CATEGORY_LABELS,
    VENDOR_LOCK_LABELS,
    analyze_and_save,
    chat_about_lot,
    dev_category_label,
    vendor_lock_label,
)
from ..db.models import (
    Announcement,
    Contract,
    Document,
    Lot,
    LotAnalysis,
    LotStatusHistory,
    Organization,
    Preset,
    ScrapeRun,
)
from ..jobs.ingest import (
    create_ingest_run,
    find_active_run,
)
from ..jobs.run_preset import _save_announcement, _save_documents
from ..jobs.scan import (
    ALL_MODES,
    MODE_FULL,
    MODE_LISTING,
    MODE_NO_HEAVY,
    create_scan_run,
    mode_flags,
)
from ..observability import setup_sentry
from ..scraper.announce import fetch_announcement
from ..scraper.http import ThrottledSession
from ..scraper.katos import BY_CODE, REGIONS, region_name
from ..scraper.statuses import (
    ACTUAL_STATUSES,
    PAST_STATUSES,
    SPECIAL_STATUSES,
    STATUS_NAMES,
    status_tone,
)
from .auth import require_auth
from .deps import format_amount, format_compact, format_dt, get_db

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["n"] = format_amount
templates.env.filters["dt"] = format_dt
templates.env.filters["compact"] = format_compact
templates.env.globals["region_name"] = region_name
templates.env.globals["dev_category_label"] = dev_category_label
templates.env.globals["vendor_lock_label"] = vendor_lock_label
templates.env.globals["status_tone"] = status_tone
templates.env.globals["status_name"] = STATUS_NAMES.get


def _nav_active(request: Request) -> str:
    """Какой пункт sidebar подсветить для текущего URL."""
    path = request.url.path
    if path == "/" or path == "":
        return "dashboard"
    if path.startswith("/starred"):
        return "starred"
    if path.startswith("/actual"):
        return "actual"
    if path.startswith("/past"):
        return "past"
    if path.startswith("/lot/"):
        # Карточка лота — drill-down из «Актуальных».
        return "actual"
    if path.startswith("/organization"):
        return "customers"
    if path.startswith("/presets"):
        return "presets"
    if path.startswith("/runs"):
        return "runs"
    if path.startswith("/ingest"):
        return "ingest"
    if path.startswith("/scan"):
        return "scan"
    return ""


templates.env.globals["nav_active"] = _nav_active

# GZ_NO_AUTH=1 отключает Basic Auth — только для dev-машины, на проде не ставится.
_AUTH_DISABLED = os.environ.get("GZ_NO_AUTH") == "1"


def _auth_dep():
    if _AUTH_DISABLED:
        return lambda: "anon"
    return require_auth


# Sentry — до создания FastAPI, чтобы интеграция перехватила middleware.
# No-op без SENTRY_DSN.
setup_sentry("web")

app = FastAPI(title="Goszakup Tracker", version=__version__)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

IT_CATEGORIES = ["Оборудование", "Услуги ИТ", "ПО и лицензии", "Связь и интернет"]
DEV_CATEGORIES = list(DEV_CATEGORY_LABELS.keys())
VENDOR_LOCK_RISKS = list(VENDOR_LOCK_LABELS.keys())
PAGE_SIZE = 50

# Кеш счётчиков сайдбара — три count() на каждый запрос дороговато впустую,
# при этом цифры могут запаздывать на минуту без вреда для UX.
_NAV_TTL_SEC = 60.0
_nav_cache: dict[str, object] = {"at": 0.0, "data": None}


def _nav_counts(db: Session) -> dict[str, int]:
    import time

    now = time.monotonic()
    cached = _nav_cache["data"]
    if cached is not None and (now - float(_nav_cache["at"])) < _NAV_TTL_SEC:
        return cached  # type: ignore[return-value]
    data = {
        "actual": db.scalar(select(func.count(Lot.id)).where(Lot.is_actual.is_(True))) or 0,
        "past": db.scalar(select(func.count(Lot.id)).where(Lot.is_actual.is_(False))) or 0,
        "starred": db.scalar(select(func.count(Lot.id)).where(Lot.is_starred.is_(True))) or 0,
        "customers": db.scalar(select(func.count(Organization.id))) or 0,
        "presets": db.scalar(select(func.count(Preset.id))) or 0,
    }
    _nav_cache["at"] = now
    _nav_cache["data"] = data
    return data


def _base_ctx(request: Request, db: Session) -> dict:
    """Общий контекст для всех шаблонов — версия и nav-счётчики."""
    return {
        "version": __version__,
        "nav_counts": _nav_counts(db),
    }


def _lots_query(
    db: Session,
    *,
    actual: bool | None,
    q: str | None,
    kato: str | None,
    it: str | None,
    dev: str | None,
    risk: str | None,
    amount_from: int | None,
    amount_to: int | None,
    starred: bool = False,
    status: int | None = None,
):
    stmt = select(Lot).options(
        selectinload(Lot.customer), selectinload(Lot.analysis)
    )
    if actual is True:
        stmt = stmt.where(Lot.is_actual.is_(True))
    elif actual is False:
        stmt = stmt.where(Lot.is_actual.is_(False))
    if starred:
        stmt = stmt.where(Lot.is_starred.is_(True))
    if status is not None:
        stmt = stmt.where(Lot.status_code == status)
    if kato:
        stmt = stmt.where(Lot.kato == kato)
    if it:
        stmt = stmt.where(Lot.it_category == it)
    if dev or risk:
        # outer join: чтобы можно было фильтровать «нет анализа» в будущем,
        # сейчас при заданном dev/risk запись анализа обязана существовать.
        stmt = stmt.join(LotAnalysis, LotAnalysis.lot_id == Lot.id, isouter=True)
        if dev:
            stmt = stmt.where(LotAnalysis.dev_category == dev)
        if risk:
            stmt = stmt.where(LotAnalysis.vendor_lock_risk == risk)
    if amount_from is not None:
        stmt = stmt.where(Lot.plan_amount >= amount_from)
    if amount_to is not None:
        stmt = stmt.where(Lot.plan_amount <= amount_to)
    if q:
        like = f"%{q}%"
        # join customer для поиска
        stmt = stmt.join(Lot.customer, isouter=True).where(
            or_(Lot.name.ilike(like), Lot.enstru.ilike(like), Organization.name.ilike(like))
        )
    return stmt


def _apply_sort(stmt, sort: str):
    cols = {
        "first_seen": Lot.first_seen,
        "last_synced": Lot.last_synced,
        "plan_amount": Lot.plan_amount,
    }
    desc_ = sort.startswith("-")
    key = sort.lstrip("-")
    col = cols.get(key, Lot.first_seen)
    return stmt.order_by(desc(col) if desc_ else col)


def _render_lots(
    request: Request,
    db: Session,
    *,
    actual: bool | None,
    base_path: str,
    title: str,
    q: str,
    kato: str,
    it: str,
    dev: str,
    risk: str,
    amount_from: int | None,
    amount_to: int | None,
    sort: str,
    page: int,
    starred: bool = False,
    status: int | None = None,
) -> HTMLResponse:
    stmt = _lots_query(
        db,
        actual=actual,
        q=q or None,
        kato=kato or None,
        it=it or None,
        dev=dev or None,
        risk=risk or None,
        amount_from=amount_from,
        amount_to=amount_to,
        starred=starred,
        status=status,
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = _apply_sort(stmt, sort)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    lots = db.scalars(stmt.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()

    filters = {
        "q": q,
        "kato": kato,
        "it": it,
        "dev": dev,
        "risk": risk,
        "amount_from": amount_from,
        "amount_to": amount_to,
        "sort": sort,
        "starred": "1" if starred else "",
        "status": status if status is not None else "",
    }

    def pagination_qs(p: int) -> str:
        d = {k: v for k, v in filters.items() if v not in (None, "")}
        d["page"] = p
        return urlencode(d, doseq=True)

    return templates.TemplateResponse(
        request,
        "lots.html",
        {
            **_base_ctx(request, db),
            "title": title,
            "lots": lots,
            "total": total,
            "page": page,
            "pages": pages,
            "filters": filters,
            "regions": REGIONS,
            "categories": IT_CATEGORIES,
            "dev_categories": DEV_CATEGORIES,
            "vendor_lock_risks": VENDOR_LOCK_RISKS,
            "actual_statuses": [(c, STATUS_NAMES[c]) for c in ACTUAL_STATUSES],
            "past_statuses": [(c, STATUS_NAMES[c]) for c in PAST_STATUSES],
            "base_path": base_path,
            "pagination_qs": pagination_qs,
        },
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _=Depends(_auth_dep())):
    stats = {
        "total_lots": db.scalar(select(func.count(Lot.id))) or 0,
        "actual_lots": db.scalar(select(func.count(Lot.id)).where(Lot.is_actual.is_(True)))
        or 0,
        "orgs": db.scalar(select(func.count(Organization.id))) or 0,
        "docs": db.scalar(select(func.count(Document.id))) or 0,
    }
    # Sparkline-серии для KPI: последние 14 прогонов по возрастанию времени.
    spark_rows = db.execute(
        select(
            ScrapeRun.listing_count,
            ScrapeRun.new_lots,
            ScrapeRun.updated_lots,
            ScrapeRun.new_documents,
        )
        .order_by(desc(ScrapeRun.started_at))
        .limit(14)
    ).all()
    spark_rows = list(reversed(spark_rows))
    spark_runs = {
        "listing": [r[0] or 0 for r in spark_rows] or [0, 0],
        "new_lots": [r[1] or 0 for r in spark_rows] or [0, 0],
        "updated": [r[2] or 0 for r in spark_rows] or [0, 0],
        "docs": [r[3] or 0 for r in spark_rows] or [0, 0],
    }
    # «Требуют внимания» — лоты с новым статусом за последние 24 часа,
    # отсортированные по сумме. Без feature starred/watched.
    since = datetime.now(UTC) - timedelta(hours=24)
    attention_lots = db.scalars(
        select(Lot)
        .where(Lot.is_actual.is_(True))
        .where(Lot.last_synced >= since)
        .order_by(desc(Lot.plan_amount))
        .options(selectinload(Lot.customer), selectinload(Lot.analysis))
        .limit(5)
    ).all()
    # Активные preset'ы для быстрого старта.
    quick_presets = db.scalars(
        select(Preset).where(Preset.active.is_(True)).order_by(Preset.id).limit(8)
    ).all()
    by_region_rows = db.execute(
        select(Lot.kato, func.count(Lot.id), func.coalesce(func.sum(Lot.plan_amount), 0))
        .where(Lot.is_actual.is_(True))
        .group_by(Lot.kato)
        .order_by(desc(func.count(Lot.id)))
    ).all()
    by_region = [
        {"region": region_name(k) or (k or "—"), "count": c, "total": t}
        for k, c, t in by_region_rows
    ]
    by_cat_rows = db.execute(
        select(Lot.it_category, func.count(Lot.id), func.coalesce(func.sum(Lot.plan_amount), 0))
        .where(Lot.is_actual.is_(True))
        .group_by(Lot.it_category)
        .order_by(desc(func.count(Lot.id)))
    ).all()
    by_category = [
        {"category": c, "count": n, "total": t} for c, n, t in by_cat_rows
    ]
    last_runs_rows = db.execute(
        select(ScrapeRun, Preset.name)
        .join(Preset, Preset.id == ScrapeRun.preset_id, isouter=True)
        .order_by(desc(ScrapeRun.started_at))
        .limit(15)
    ).all()
    last_runs = []
    for run, name in last_runs_rows:
        last_runs.append(
            {
                "started_at": run.started_at,
                "preset_name": name,
                "listing_count": run.listing_count,
                "new_lots": run.new_lots,
                "updated_lots": run.updated_lots,
                "new_documents": run.new_documents,
                "errors": run.errors,
            }
        )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            **_base_ctx(request, db),
            "stats": stats,
            "by_region": by_region,
            "by_category": by_category,
            "last_runs": last_runs,
            "spark_runs": spark_runs,
            "attention_lots": attention_lots,
            "quick_presets": quick_presets,
        },
    )


# amount_from/amount_to идут из <input type="number"> — браузер шлёт пустую
# строку при пустом поле (?amount_from=). FastAPI с типом int | None парсит
# это в int и валится. Принимаем как str и приводим вручную.
def _maybe_int(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


@app.get("/actual", response_class=HTMLResponse)
def actual_lots(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
    q: str = "",
    kato: str = "",
    it: str = "",
    dev: str = "",
    risk: str = "",
    amount_from: str = "",
    amount_to: str = "",
    sort: str = "-first_seen",
    page: int = 1,
    starred: str = "",
    status: str = "",
):
    return _render_lots(
        request, db,
        actual=True, base_path="/actual", title="Актуальные тендеры",
        q=q, kato=kato, it=it, dev=dev, risk=risk,
        amount_from=_maybe_int(amount_from), amount_to=_maybe_int(amount_to),
        sort=sort, page=page, starred=(starred == "1"),
        status=_maybe_int(status),
    )


@app.get("/past", response_class=HTMLResponse)
def past_lots(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
    q: str = "",
    kato: str = "",
    it: str = "",
    dev: str = "",
    risk: str = "",
    amount_from: str = "",
    amount_to: str = "",
    sort: str = "-first_seen",
    page: int = 1,
    starred: str = "",
    status: str = "",
):
    return _render_lots(
        request, db,
        actual=False, base_path="/past", title="Прошедшие тендеры",
        q=q, kato=kato, it=it, dev=dev, risk=risk,
        amount_from=_maybe_int(amount_from), amount_to=_maybe_int(amount_to),
        sort=sort, page=page, starred=(starred == "1"),
        status=_maybe_int(status),
    )


@app.get("/starred", response_class=HTMLResponse)
def starred_lots(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
    q: str = "",
    kato: str = "",
    it: str = "",
    dev: str = "",
    risk: str = "",
    amount_from: str = "",
    amount_to: str = "",
    sort: str = "-first_seen",
    page: int = 1,
    only: str = "",  # "actual" | "past" | "" (все)
    status: str = "",
):
    actual_filter = None
    if only == "actual":
        actual_filter = True
    elif only == "past":
        actual_filter = False
    return _render_lots(
        request, db,
        actual=actual_filter, base_path="/starred", title="Избранные тендеры",
        q=q, kato=kato, it=it, dev=dev, risk=risk,
        amount_from=_maybe_int(amount_from), amount_to=_maybe_int(amount_to),
        sort=sort, page=page, starred=True,
        status=_maybe_int(status),
    )


@app.get("/lot/{lot_id}", response_class=HTMLResponse)
def lot_detail(
    lot_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    lot = db.scalar(
        select(Lot)
        .where(Lot.id == lot_id)
        .options(
            selectinload(Lot.customer),
            selectinload(Lot.analysis),
            selectinload(Lot.announcement).selectinload(Announcement.organizer),
            selectinload(Lot.announcement).selectinload(Announcement.documents),
        )
    )
    if lot is None:
        raise HTTPException(404, "лот не найден")
    history = db.scalars(
        select(LotStatusHistory)
        .where(LotStatusHistory.lot_id == lot_id)
        .order_by(desc(LotStatusHistory.observed_at))
    ).all()
    documents = lot.announcement.documents if lot.announcement else []
    contracts = db.scalars(
        select(Contract)
        .where(Contract.lot_id == lot_id)
        .options(selectinload(Contract.supplier))
    ).all()
    has_downloaded_doc = any(d.local_path for d in documents)
    analyze_status = request.query_params.get("analyzed")
    fetched_docs = request.query_params.get("docs")
    fetch_error = request.query_params.get("fetch_error")
    return templates.TemplateResponse(
        request,
        "lot.html",
        {
            **_base_ctx(request, db),
            "lot": lot,
            "history": history,
            "documents": documents,
            "contracts": contracts,
            "has_downloaded_doc": has_downloaded_doc,
            "analyze_status": analyze_status,
            "fetched_docs": fetched_docs,
            "fetch_error": fetch_error,
        },
    )


class _ChatMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str = Field(min_length=1, max_length=8000)


class _ChatRequest(BaseModel):
    # Ограничиваем суммарную длину истории — пусть фронт обрезает старые
    # сообщения, не плодим бесконечный контекст.
    messages: list[_ChatMessage] = Field(min_length=1, max_length=40)


@app.post("/lot/{lot_id}/chat")
def lot_chat(
    lot_id: int,
    body: _ChatRequest,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    lot = db.scalar(
        select(Lot)
        .where(Lot.id == lot_id)
        .options(
            selectinload(Lot.customer),
            selectinload(Lot.announcement).selectinload(Announcement.documents),
        )
    )
    if lot is None:
        raise HTTPException(404, "лот не найден")
    if body.messages[-1].role != "user":
        raise HTTPException(400, "последнее сообщение должно быть от user")
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    try:
        reply = chat_about_lot(lot, history)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"внутренняя ошибка: {e}"}, status_code=500)
    return {"reply": reply}


@app.post("/lot/{lot_id}/fetch_documents")
def lot_fetch_documents(
    lot_id: int,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    lot = db.scalar(
        select(Lot)
        .where(Lot.id == lot_id)
        .options(selectinload(Lot.announcement).selectinload(Announcement.documents))
    )
    if lot is None:
        raise HTTPException(404, "лот не найден")
    if lot.announcement_id is None:
        return RedirectResponse(
            f"/lot/{lot_id}?fetch_error=no_announcement", status_code=303
        )
    # Свежая ThrottledSession на запрос: Crawl-delay внутри инстанса, на пару
    # секунд параллельная нагрузка превысит лимит — приемлемо для ручной
    # кнопки. Не нажимать одновременно с запущенным `daily`.
    http = ThrottledSession()
    try:
        # `_save_announcement` создаст запись Announcement, если её ещё нет
        # (типичный кейс для stub-лота из листинга без фазы details).
        detail = fetch_announcement(lot.announcement_id, session=http)
        anno = _save_announcement(db, detail)
        new_count = _save_documents(db, anno, detail, http)
        db.commit()
    except Exception as e:
        db.rollback()
        log = __import__("logging").getLogger(__name__)
        log.exception("fetch_documents failed for lot %s: %s", lot_id, e)
        return RedirectResponse(
            f"/lot/{lot_id}?fetch_error=1", status_code=303
        )
    return RedirectResponse(f"/lot/{lot_id}?docs={new_count}", status_code=303)


@app.post("/lot/{lot_id}/star")
def lot_toggle_star(
    lot_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
    next: str = Form(""),
):
    lot = db.get(Lot, lot_id)
    if lot is None:
        raise HTTPException(404, "лот не найден")
    lot.is_starred = not lot.is_starred
    db.commit()
    # Сбрасываем кеш счётчиков sidebar — иначе «Избранное» залипает на минуту.
    _nav_cache["at"] = 0.0
    # Поддерживаем два варианта: fetch() из JS (ждёт JSON) и обычная HTML-форма.
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return {"starred": lot.is_starred}
    target = next or f"/lot/{lot_id}"
    return RedirectResponse(target, status_code=303)


@app.post("/lot/{lot_id}/analyze")
def lot_analyze(
    lot_id: int,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    lot = db.scalar(
        select(Lot)
        .where(Lot.id == lot_id)
        .options(
            selectinload(Lot.customer),
            selectinload(Lot.analysis),
            selectinload(Lot.announcement).selectinload(Announcement.documents),
        )
    )
    if lot is None:
        raise HTTPException(404, "лот не найден")
    ok = analyze_and_save(db, lot, force=True)
    if ok:
        db.commit()
    return RedirectResponse(
        f"/lot/{lot_id}?analyzed={'ok' if ok else 'fail'}",
        status_code=303,
    )


@app.get("/document/{doc_id}/download")
def document_download(
    doc_id: int,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    doc = db.get(Document, doc_id)
    if not doc or not doc.local_path or not Path(doc.local_path).exists():
        raise HTTPException(404, "файл не найден")
    return FileResponse(
        doc.local_path,
        media_type=doc.content_type or "application/octet-stream",
        filename=Path(doc.local_path).name,
    )


@app.get("/organizations", response_class=HTMLResponse)
def organizations_list(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
    q: str = "",
    sort: str = "-total",
    page: int = 1,
):
    base = (
        select(
            Organization.id,
            Organization.bin,
            Organization.name,
            func.count(Lot.id).label("lots_cnt"),
            func.coalesce(func.sum(Lot.plan_amount), 0).label("total"),
            # `case` — стандартный SQL и работает в SQLite и Postgres.
            # Раньше тут был `func.iif`, но он SQLite-only — на Postgres падало.
            func.sum(case((Lot.is_actual, 1), else_=0)).label("actual_cnt"),
        )
        .join(Lot, Lot.customer_id == Organization.id, isouter=True)
        .group_by(Organization.id)
    )
    if q:
        like = f"%{q}%"
        base = base.where(or_(Organization.name.ilike(like), Organization.bin.ilike(like)))

    cols = {
        "name": Organization.name,
        "lots": func.count(Lot.id),
        "total": func.coalesce(func.sum(Lot.plan_amount), 0),
    }
    desc_ = sort.startswith("-")
    col = cols.get(sort.lstrip("-"), cols["total"])
    base = base.order_by(desc(col) if desc_ else col)

    total = db.scalar(select(func.count()).select_from(Organization))
    pages = max(1, ((total or 0) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    rows = db.execute(base.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()

    # Доля топ-10 в общей сумме (для подзаголовка).
    total_sum = db.scalar(
        select(func.coalesce(func.sum(Lot.plan_amount), 0))
    ) or 0
    top10_rows = db.execute(
        select(func.coalesce(func.sum(Lot.plan_amount), 0).label("total"))
        .select_from(Organization)
        .join(Lot, Lot.customer_id == Organization.id, isouter=True)
        .group_by(Organization.id)
        .order_by(desc(func.coalesce(func.sum(Lot.plan_amount), 0)))
        .limit(10)
    ).all()
    top10_sum = sum((r.total or 0) for r in top10_rows)
    top10_share = round(top10_sum * 100 / total_sum) if total_sum else 0

    return templates.TemplateResponse(
        request,
        "organizations.html",
        {
            **_base_ctx(request, db),
            "rows": rows,
            "q": q,
            "sort": sort,
            "page": page,
            "pages": pages,
            "total": total,
            "top10_share": top10_share,
        },
    )


@app.get("/organization/{org_id}", response_class=HTMLResponse)
def organization_detail(
    org_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "не найдено")
    lots = db.scalars(
        select(Lot)
        .where(Lot.customer_id == org_id)
        .order_by(desc(Lot.first_seen))
        .options(selectinload(Lot.customer))
    ).all()
    actual = [lt for lt in lots if lt.is_actual]
    past = [lt for lt in lots if not lt.is_actual]
    total_plan = sum((lt.plan_amount or 0) for lt in lots)
    return templates.TemplateResponse(
        request,
        "organization.html",
        {
            **_base_ctx(request, db),
            "org": org,
            "actual": actual,
            "past": past,
            "total_plan": total_plan,
        },
    )


@app.get("/presets", response_class=HTMLResponse)
def presets_list(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    presets = db.scalars(select(Preset).order_by(Preset.id)).all()
    return templates.TemplateResponse(
        request,
        "presets.html",
        {**_base_ctx(request, db), "presets": presets, "region_lookup": BY_CODE},
    )


@app.post("/presets/{preset_id}/toggle")
def preset_toggle(
    preset_id: int,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    p = db.get(Preset, preset_id)
    if not p:
        raise HTTPException(404)
    p.active = not p.active
    db.commit()
    return RedirectResponse("/presets", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def runs_list(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    rows = db.execute(
        select(ScrapeRun, Preset.name)
        .join(Preset, Preset.id == ScrapeRun.preset_id, isouter=True)
        .order_by(desc(ScrapeRun.started_at))
        .limit(200)
    ).all()
    runs = [
        {
            "id": r.id,
            "preset_name": name,
            "note": r.note,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "listing_count": r.listing_count,
            "new_lots": r.new_lots,
            "updated_lots": r.updated_lots,
            "details_fetched": r.details_fetched,
            "new_documents": r.new_documents,
            "llm_analyzed": r.llm_analyzed,
            "errors": r.errors,
        }
        for r, name in rows
    ]
    return templates.TemplateResponse(
        request, "runs.html", {**_base_ctx(request, db), "runs": runs}
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
):
    row = db.execute(
        select(ScrapeRun, Preset.name)
        .join(Preset, Preset.id == ScrapeRun.preset_id, isouter=True)
        .where(ScrapeRun.id == run_id)
    ).first()
    if row is None:
        raise HTTPException(404, "прогон не найден")
    run, preset_name = row
    # Лоты, у которых first_seen или last_synced попадают в окно работы run'а.
    # Грубое приближение «лоты этого прогона»: last_synced между started_at и
    # finished_at (или now() если ещё идёт).
    upper = run.finished_at or datetime.now(UTC)
    lots = db.scalars(
        select(Lot)
        .where(Lot.last_synced >= run.started_at)
        .where(Lot.last_synced <= upper)
        .order_by(desc(Lot.last_synced))
        .options(selectinload(Lot.customer))
        .limit(500)
    ).all()
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            **_base_ctx(request, db),
            "run": run,
            "preset_name": preset_name,
            "lots": lots,
            "in_progress": run.finished_at is None,
        },
    )


# Множества статусов для UI; для шаблона удобнее иметь готовые списки кортежей.
_STATUS_GROUPS = [
    ("Актуальные", [(c, STATUS_NAMES[c]) for c in ACTUAL_STATUSES]),
    ("Прошедшие", [(c, STATUS_NAMES[c]) for c in PAST_STATUSES]),
    ("Особые", [(c, STATUS_NAMES[c]) for c in SPECIAL_STATUSES]),
]
_TRADE_TYPES = [
    ("", "— любое —"),
    ("g", "Товары"),
    ("s", "Услуги"),
    ("r", "Работы"),
]


@app.get("/ingest", response_class=HTMLResponse)
def ingest_form(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
    customer_bin: str = "",
    error: str = "",
):
    current_year = datetime.now(UTC).year
    active = find_active_run(db)
    return templates.TemplateResponse(
        request,
        "ingest.html",
        {
            **_base_ctx(request, db),
            "status_groups": _STATUS_GROUPS,
            "trade_types": _TRADE_TYPES,
            "defaults": {
                "customer_bin": customer_bin,
                "year_from": current_year - 1,
                "year_to": current_year,
                "amount_from": 0,
            },
            "active_run": active,
            "error": error,
        },
    )


@app.post("/ingest/run")
def ingest_start(
    customer_bin: str = Form(...),
    year_from: int = Form(...),
    year_to: int = Form(...),
    trade_type: str = Form(""),
    status: list[int] = Form(default=[]),
    amount_from: int = Form(0),
    amount_to: str = Form(""),
    _=Depends(_auth_dep()),
):
    customer_bin = customer_bin.strip()
    if not customer_bin.isdigit() or len(customer_bin) != 12:
        return RedirectResponse(
            f"/ingest?customer_bin={customer_bin}&error=bin_invalid",
            status_code=303,
        )
    if year_from > year_to:
        return RedirectResponse(
            f"/ingest?customer_bin={customer_bin}&error=year_range",
            status_code=303,
        )
    amount_to_int = _maybe_int(amount_to)
    if trade_type not in ("", "g", "s", "r"):
        trade_type = ""

    try:
        run_id = create_ingest_run(
            customer_bin=customer_bin,
            year_from=year_from,
            year_to=year_to,
            trade_type=trade_type,
            status_codes=status,
            amount_from=amount_from,
            amount_to=amount_to_int,
        )
    except RuntimeError:
        return RedirectResponse(
            f"/ingest?customer_bin={customer_bin}&error=busy",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(
            f"/ingest?customer_bin={customer_bin}&error={e}",
            status_code=303,
        )

    # Phase 3: отправляем в очередь Dramatiq, а не запускаем в BackgroundTasks.
    # Это пережил бы рестарт uvicorn — worker подберёт сообщение позже.
    from ..queue.actors import ingest_actor
    ingest_actor.send(
        run_id,
        customer_bin,
        year_from,
        year_to,
        trade_type,
        list(status),
        amount_from,
        amount_to_int,
    )
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


# === /scan — ad-hoc прогон по kato/amount/status/IT-категориям ===


_SCAN_MODES = [
    (MODE_LISTING, "Только листинг", "Быстрый разведочный проход — без HTTP-нагрузки на детали."),
    (MODE_NO_HEAVY, "Листинг + детали", "Тянет organizer, контакты и договоры. Без документов и LLM."),
    (MODE_FULL, "Полный (как daily)", "Детали + документы + LLM-анализ для IT-лотов."),
]


@app.get("/scan", response_class=HTMLResponse)
def scan_form(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(_auth_dep()),
    error: str = "",
):
    active = find_active_run(db)
    return templates.TemplateResponse(
        request,
        "scan.html",
        {
            **_base_ctx(request, db),
            "regions": REGIONS,
            "status_groups": _STATUS_GROUPS,
            "it_categories": IT_CATEGORIES,
            "scan_modes": _SCAN_MODES,
            "defaults": {
                "kato": "",
                "amount_from": 500_000,
                "amount_to": "",
                "mode": MODE_FULL,
                "actual_preselected": True,
            },
            "active_run": active,
            "error": error,
        },
    )


@app.post("/scan/run")
def scan_start(
    kato: str = Form(""),
    amount_from: int = Form(0),
    amount_to: str = Form(""),
    status: list[int] = Form(default=[]),
    it: list[str] = Form(default=[]),
    mode: str = Form(MODE_FULL),
    _=Depends(_auth_dep()),
):
    kato = (kato or "").strip()
    if kato and kato not in BY_CODE:
        return RedirectResponse("/scan?error=kato_invalid", status_code=303)
    if amount_from < 0:
        return RedirectResponse("/scan?error=amount_from_negative", status_code=303)
    amount_to_int = _maybe_int(amount_to)
    if amount_to_int is not None and amount_to_int < amount_from:
        return RedirectResponse("/scan?error=amount_range", status_code=303)
    if mode not in ALL_MODES:
        return RedirectResponse("/scan?error=mode_invalid", status_code=303)
    # Чекбоксы IT приходят строками — отфильтруем мусор.
    it_clean = [c for c in (it or []) if c in IT_CATEGORIES]
    status_clean = list(status or [])

    try:
        run_id = create_scan_run(
            kato=kato,
            amount_from=amount_from,
            amount_to=amount_to_int,
            status_codes=status_clean,
            it_categories=it_clean,
            mode=mode,
        )
    except RuntimeError:
        return RedirectResponse("/scan?error=busy", status_code=303)
    except ValueError as e:
        return RedirectResponse(f"/scan?error={e}", status_code=303)

    listing_only, with_docs, with_llm = mode_flags(mode)

    from ..queue.actors import scan_actor
    scan_actor.send(
        run_id,
        kato,
        amount_from,
        amount_to_int,
        status_clean,
        it_clean,
        listing_only,
        with_docs,
        with_llm,
    )
    return RedirectResponse(f"/runs/{run_id}", status_code=303)
