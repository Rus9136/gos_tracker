"""FastAPI приложение."""

from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__
from ..autosubmit.rpc import RunResult
from ..autosubmit.scheduler import apply_result
from ..classify.llm import (
    DEV_CATEGORY_LABELS,
    VENDOR_LOCK_LABELS,
    analyze_and_save,
    chat_about_lot,
    dev_category_label,
    vendor_lock_label,
)
from ..classify.usage import record_call
from ..classify.verticals import VERTICAL_HUES, VERTICAL_LABELS, VERTICALS
from ..config import (
    AUTOSUBMIT_INGEST_TOKEN,
    GZ_TELEGRAM_BOT_TOKEN,
    LLM_PRICE_INPUT_PER_MTOK,
    LLM_PRICE_OUTPUT_PER_MTOK,
    PUBLIC_BASE_URL,
    SECRET_KEY,
    TELEGRAM_WEBHOOK_SECRET,
    require_safe_secret_key,
)
from ..db.engine import SessionLocal
from ..db.models import (
    Announcement,
    ClientCredential,
    Contract,
    Document,
    LlmCall,
    Lot,
    LotAnalysis,
    LotBid,
    LotStatusHistory,
    Organization,
    Preset,
    Role,
    ScrapeRun,
    Submission,
    User,
    UserLotMatch,
    UserQuery,
)
from ..jobs.ingest import (
    close_stale_runs,
    create_ingest_run,
    find_active_run,
)
from ..jobs.org_report import build_org_report, related_org_ids, render_markdown
from ..jobs.run_preset import _save_announcement, _save_documents
from ..jobs.scan import (
    ALL_MODES,
    MODE_FULL,
    MODE_LISTING,
    MODE_NO_HEAVY,
    create_scan_run,
    mode_flags,
)
from ..jobs.supplier_report import (
    SupplierFilters,
    build_supplier_report,
)
from ..jobs.supplier_report import (
    render_csv as render_suppliers_csv,
)
from ..observability import setup_sentry
from ..scope import lot_in_scope, scope_conditions
from ..scraper.katos import BY_CODE, REGIONS, region_name
from ..scraper.statuses import (
    ACTUAL_STATUSES,
    PAST_STATUSES,
    SPECIAL_STATUSES,
    STATUS_NAMES,
    status_tone,
)
from ..sources import make_source
from ..watchlist import should_analyze
from .auth import (
    NotAuthenticated,
    authenticate,
    hash_password,
    require_admin,
    require_user,
    seed_admin_from_env,
)
from .deps import format_amount, format_compact, format_dt, format_iso, get_db
from .pages import (
    ALL_KEYS,
    ALL_PAGES,
    PAGES,
    SYSTEM_PAGES,
    allowed_pages,
    first_allowed_path,
    require_page,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["n"] = format_amount
templates.env.filters["dt"] = format_dt
templates.env.filters["compact"] = format_compact
templates.env.filters["iso"] = format_iso
# Слаг вертикали -> русский лейбл (незнакомое значение показываем как есть).
# "other" — псевдо-слаг UI-фильтров для category IS NULL, в реестре его нет.
templates.env.filters["vertical"] = lambda s: VERTICAL_LABELS.get(
    s, "Прочее" if s == "other" else (s or "—")
)
templates.env.globals["VERTICAL_HUES"] = VERTICAL_HUES
templates.env.globals["region_name"] = region_name
templates.env.globals["dev_category_label"] = dev_category_label
templates.env.globals["vendor_lock_label"] = vendor_lock_label
templates.env.globals["status_tone"] = status_tone
templates.env.globals["status_name"] = STATUS_NAMES.get


def static_url(path: str) -> str:
    # Cache-busting по mtime файла: при каждом изменении статики URL меняется,
    # браузер перекачивает её, а не отдаёт устаревшую из кеша. __version__ для
    # этого не годится — он не меняется между деплоями.
    rel = path.lstrip("/")
    try:
        mtime = int((STATIC_DIR / rel).stat().st_mtime)
    except OSError:
        mtime = 0
    return f"/static/{rel}?v={mtime}"


templates.env.globals["static_url"] = static_url


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
    if path.startswith("/suppliers"):
        return "suppliers"
    if path.startswith("/matched"):
        return "matched"
    if path.startswith("/queries"):
        return "queries"
    if path.startswith("/settings"):
        return "settings"
    if path.startswith("/presets"):
        return "presets"
    if path.startswith("/runs"):
        return "runs"
    if path.startswith("/ingest"):
        return "ingest"
    if path.startswith("/scan"):
        return "scan"
    if path.startswith("/submissions"):
        return "submissions"
    if path.startswith("/roles"):
        return "roles"
    if path.startswith("/users"):
        return "users"
    if path.startswith("/expenses"):
        return "expenses"
    return ""


templates.env.globals["nav_active"] = _nav_active


# Sentry — до создания FastAPI, чтобы интеграция перехватила middleware.
# No-op без SENTRY_DSN.
setup_sentry("web")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Fail-fast: боевой web не должен подниматься на публичном dev-дефолте
    # SECRET_KEY (иначе подделка cookie-сессии = обход auth). См. config.
    require_safe_secret_key("web")
    # Плавная миграция прода: первый запуск с пустой users-таблицей и заданными
    # GZ_USER/GZ_PASSWORD заведёт админа из них. См. auth.seed_admin_from_env.
    db = SessionLocal()
    try:
        seed_admin_from_env(db)
    finally:
        db.close()
    yield


app = FastAPI(title="Goszakup Tracker", version=__version__, lifespan=_lifespan)
# Подписанная cookie-сессия — хранит только uid вошедшего пользователя.
# https_only можно ужесточить за nginx (TLS терминируется там), оставляем
# мягким, чтобы работало и на dev по http.
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="gz_session")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(NotAuthenticated)
def _redirect_to_login(request: Request, exc: NotAuthenticated):
    nxt = urlencode({"next": exc.next_url}) if exc.next_url and exc.next_url != "/" else ""
    return RedirectResponse(f"/login?{nxt}" if nxt else "/login", status_code=303)


# Вертикали (classify/verticals.py): в форме/URL ходят слаги, лейблы — для UI.
CATEGORY_CHOICES = [(v.slug, v.name_ru) for v in VERTICALS]
CATEGORY_SLUGS = set(VERTICAL_LABELS)
DEV_CATEGORIES = list(DEV_CATEGORY_LABELS.keys())
VENDOR_LOCK_RISKS = list(VENDOR_LOCK_LABELS.keys())
PAGE_SIZE = 50

# Scope-логика вынесена в goszakup.scope (её переиспользует matcher-fan-out).
# Локальные алиасы сохранены, чтобы не трогать многочисленные call-sites.
_scope_conditions = scope_conditions
_lot_in_scope = lot_in_scope


# Кеш счётчиков сайдбара — три count() на каждый запрос дороговато впустую,
# при этом цифры могут запаздывать на минуту без вреда для UX. Ключ кеша —
# uid пользователя: scope у каждого свой, поэтому и счётчики разные.
_NAV_TTL_SEC = 60.0
_nav_cache: dict[int, dict] = {}


def _nav_counts(db: Session, user: User | None) -> dict[str, int]:
    import time

    uid = user.id if user is not None else -1
    now = time.monotonic()
    cached = _nav_cache.get(uid)
    if cached is not None and (now - cached["at"]) < _NAV_TTL_SEC:
        return cached["data"]
    scope = _scope_conditions(user)

    def _count(*extra):
        stmt = select(func.count(Lot.id))
        for c in (*scope, *extra):
            stmt = stmt.where(c)
        return db.scalar(stmt) or 0

    # «Подходящие» — уникальные лоты, сматченные под запросы этого пользователя.
    # Лот, попавший под несколько запросов, считается один раз (distinct lot_id),
    # как и на странице /matched список — про лоты, а не про пары.
    matched = 0
    if user is not None:
        matched = db.scalar(
            select(func.count(func.distinct(UserLotMatch.lot_id)))
            .join(UserQuery, UserLotMatch.user_query_id == UserQuery.id)
            .join(Lot, UserLotMatch.lot_id == Lot.id)
            .where(
                UserQuery.user_id == user.id,
                UserQuery.active.is_(True),
                UserLotMatch.matched.is_(True),
                Lot.is_actual.is_(True),
            )
        ) or 0

    data = {
        "actual": _count(Lot.is_actual.is_(True)),
        "past": _count(Lot.is_actual.is_(False)),
        "starred": _count(Lot.is_starred.is_(True)),
        "matched": matched,
        "customers": db.scalar(select(func.count(Organization.id))) or 0,
        "presets": db.scalar(select(func.count(Preset.id))) or 0,
    }
    _nav_cache[uid] = {"at": now, "data": data}
    return data


def _bust_nav_cache() -> None:
    _nav_cache.clear()


def _base_ctx(request: Request, db: Session, user: User | None) -> dict:
    """Общий контекст для всех шаблонов — версия, текущий пользователь,
    nav-счётчики (в его scope) и разрешённые вкладки его роли."""
    return {
        "version": __version__,
        "user": user,
        "nav_counts": _nav_counts(db, user),
        "perms": allowed_pages(user),
    }


def _lots_query(
    db: Session,
    *,
    user: User | None,
    actual: bool | None,
    q: str | None,
    kato: str | None,
    category: str | None,
    dev: str | None,
    risk: str | None,
    amount_from: int | None,
    amount_to: int | None,
    starred: bool = False,
    status: int | None = None,
):
    stmt = select(Lot).options(
        selectinload(Lot.customer),
        selectinload(Lot.analysis),
        # announcement нужен для дедлайна (таймер обратного отсчёта) и номера
        # объявления в списке — без eager-load шаблон лениво дёргал бы его на
        # каждой строке (N+1).
        selectinload(Lot.announcement),
    )
    # Persona scope — режет выборку под регионы/категории/сумму пользователя
    # ещё до пользовательских фильтров формы. Для админа условий нет.
    for cond in _scope_conditions(user):
        stmt = stmt.where(cond)
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
    if category == "other":
        # Псевдо-слаг UI: лоты вне вертикалей («прочее»).
        stmt = stmt.where(Lot.category.is_(None))
    elif category:
        stmt = stmt.where(Lot.category == category)
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
    user: User | None,
    actual: bool | None,
    base_path: str,
    title: str,
    q: str,
    kato: str,
    category: str,
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
        user=user,
        actual=actual,
        q=q or None,
        kato=kato or None,
        category=category or None,
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
        "category": category,
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
            **_base_ctx(request, db, user),
            "title": title,
            "lots": lots,
            "total": total,
            "page": page,
            "pages": pages,
            "filters": filters,
            "regions": REGIONS,
            "categories": CATEGORY_CHOICES,
            "dev_categories": DEV_CATEGORIES,
            "vendor_lock_risks": VENDOR_LOCK_RISKS,
            "actual_statuses": [(c, STATUS_NAMES[c]) for c in ACTUAL_STATUSES],
            "past_statuses": [(c, STATUS_NAMES[c]) for c in PAST_STATUSES],
            "base_path": base_path,
            "pagination_qs": pagination_qs,
        },
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    # "/" — точка входа после логина, поэтому закрытый ролью дашборд не 403,
    # а редирект на первую разрешённую вкладку (роль совсем без вкладок — 403).
    if "dashboard" not in allowed_pages(user):
        target = first_allowed_path(user)
        if target is None:
            raise HTTPException(403, "раздел недоступен для вашей роли")
        return RedirectResponse(target, status_code=303)
    # scope пользователя — режем все лот-метрики дашборда под его регионы/
    # категории/сумму. Прогоны и счётчики org/doc остаются глобальными.
    scope = _scope_conditions(user)

    def _scoped(stmt):
        for c in scope:
            stmt = stmt.where(c)
        return stmt

    stats = {
        "total_lots": db.scalar(_scoped(select(func.count(Lot.id)))) or 0,
        "actual_lots": db.scalar(
            _scoped(select(func.count(Lot.id)).where(Lot.is_actual.is_(True)))
        )
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
        _scoped(
            select(Lot)
            .where(Lot.is_actual.is_(True))
            .where(Lot.last_synced >= since)
            .order_by(desc(Lot.plan_amount))
            .options(selectinload(Lot.customer), selectinload(Lot.analysis))
            .limit(5)
        )
    ).all()
    # Активные preset'ы для быстрого старта.
    quick_presets = db.scalars(
        select(Preset).where(Preset.active.is_(True)).order_by(Preset.id).limit(8)
    ).all()
    by_region_rows = db.execute(
        _scoped(
            select(Lot.kato, func.count(Lot.id), func.coalesce(func.sum(Lot.plan_amount), 0))
            .where(Lot.is_actual.is_(True))
            .group_by(Lot.kato)
            .order_by(desc(func.count(Lot.id)))
        )
    ).all()
    by_region = [
        {"region": region_name(k) or (k or "—"), "count": c, "total": t}
        for k, c, t in by_region_rows
    ]
    by_cat_rows = db.execute(
        _scoped(
            select(Lot.category, func.count(Lot.id), func.coalesce(func.sum(Lot.plan_amount), 0))
            .where(Lot.is_actual.is_(True))
            .group_by(Lot.category)
            .order_by(desc(func.count(Lot.id)))
        )
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
            **_base_ctx(request, db, user),
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
    user: User = Depends(require_page("actual")),
    q: str = "",
    kato: str = "",
    category: str = "",
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
        request, db, user=user,
        actual=True, base_path="/actual", title="Актуальные тендеры",
        q=q, kato=kato, category=category, dev=dev, risk=risk,
        amount_from=_maybe_int(amount_from), amount_to=_maybe_int(amount_to),
        sort=sort, page=page, starred=(starred == "1"),
        status=_maybe_int(status),
    )


@app.get("/past", response_class=HTMLResponse)
def past_lots(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("past")),
    q: str = "",
    kato: str = "",
    category: str = "",
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
        request, db, user=user,
        actual=False, base_path="/past", title="Прошедшие тендеры",
        q=q, kato=kato, category=category, dev=dev, risk=risk,
        amount_from=_maybe_int(amount_from), amount_to=_maybe_int(amount_to),
        sort=sort, page=page, starred=(starred == "1"),
        status=_maybe_int(status),
    )


@app.get("/starred", response_class=HTMLResponse)
def starred_lots(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("starred")),
    q: str = "",
    kato: str = "",
    category: str = "",
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
        request, db, user=user,
        actual=actual_filter, base_path="/starred", title="Избранные тендеры",
        q=q, kato=kato, category=category, dev=dev, risk=risk,
        amount_from=_maybe_int(amount_from), amount_to=_maybe_int(amount_to),
        sort=sort, page=page, starred=True,
        status=_maybe_int(status),
    )


@app.get("/lot/{lot_id}", response_class=HTMLResponse)
def lot_detail(
    lot_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
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
    if lot is None or not _lot_in_scope(lot, user):
        # Вне scope отдаём 404 (а не 403) — не раскрываем существование лота.
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
    # Заявки конкурентов — по возрастанию цены: первым идёт самый дешёвый,
    # так карточка сразу читается как «кто и насколько дал ниже».
    bids = db.scalars(
        select(LotBid)
        .where(LotBid.lot_id == lot_id)
        .options(selectinload(LotBid.supplier))
        .order_by(LotBid.price.nulls_last(), LotBid.date_apply)
    ).all()
    has_downloaded_doc = any(d.local_path for d in documents)
    analyze_status = request.query_params.get("analyzed")
    fetched_docs = request.query_params.get("docs")
    fetch_error = request.query_params.get("fetch_error")
    return templates.TemplateResponse(
        request,
        "lot.html",
        {
            **_base_ctx(request, db, user),
            "lot": lot,
            "history": history,
            "documents": documents,
            "contracts": contracts,
            "bids": bids,
            "has_downloaded_doc": has_downloaded_doc,
            # Гейт кнопки «Переанализировать»: _analyze_inner молча откажет
            # лоту вне watchlist — не показываем кнопку, которая не сработает.
            "can_analyze": should_analyze(db, lot),
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
    user: User = Depends(require_user),
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
        reply, usage = chat_about_lot(lot, history)
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"внутренняя ошибка: {e}"}, status_code=500)
    record_call(db, "chat", usage, lot_id=lot.id, user_id=getattr(user, "id", None))
    db.commit()
    return {"reply": reply}


@app.post("/lot/{lot_id}/fetch_documents")
def lot_fetch_documents(
    lot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
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
    # Свежий источник на запрос: при HTML-фолбэке Crawl-delay живёт внутри
    # инстанса, на пару секунд параллельная нагрузка превысит лимит —
    # приемлемо для ручной кнопки. Не нажимать одновременно с `daily`.
    source = make_source()
    try:
        # `_save_announcement` создаст запись Announcement, если её ещё нет
        # (типичный кейс для stub-лота из листинга без фазы details).
        detail = source.fetch_announcement(lot.announcement_id)
        anno = _save_announcement(db, detail)
        new_count = _save_documents(db, anno, detail, source)
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
    user: User = Depends(require_user),
    next: str = Form(""),
):
    lot = db.get(Lot, lot_id)
    if lot is None:
        raise HTTPException(404, "лот не найден")
    lot.is_starred = not lot.is_starred
    db.commit()
    # Сбрасываем кеш счётчиков sidebar — иначе «Избранное» залипает на минуту.
    _bust_nav_cache()
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
    user: User = Depends(require_admin),
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
        # Анализ изменился → матчи против старого tz_summary устарели.
        # Идемпотентность матчера сама увидит свежий analyzed_at и пересчитает.
        try:
            from ..queue.matching import enqueue_matches_for_lot

            enqueue_matches_for_lot(db, lot)
        except Exception as e:  # noqa: BLE001 — брокер недоступен ≠ ошибка анализа
            import logging

            logging.getLogger(__name__).warning(
                "fan-out матчинга для лота %s не поставлен: %s", lot_id, e
            )
    return RedirectResponse(
        f"/lot/{lot_id}?analyzed={'ok' if ok else 'fail'}",
        status_code=303,
    )


@app.get("/document/{doc_id}/download")
def document_download(
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    doc = db.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "файл не найден")
    # IDOR-защита: документ показывается на карточке лота, поэтому доступ к нему
    # гейтим тем же scope. Отдаём, только если хотя бы один лот его объявления в
    # scope пользователя. Вне scope — 404 (не раскрываем существование), как на
    # /lot/{id}. Проверяем ДО обращения к диску.
    if not user.is_admin:
        in_scope = db.scalar(
            select(Lot.id)
            .where(Lot.announcement_id == doc.announcement_id, *scope_conditions(user))
            .limit(1)
        )
        if in_scope is None:
            raise HTTPException(404, "файл не найден")
    if not doc.local_path or not Path(doc.local_path).exists():
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
    user: User = Depends(require_page("organizations")),
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
            **_base_ctx(request, db, user),
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
    user: User = Depends(require_page("organizations")),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "не найдено")
    # Одна организация в БД часто живёт двумя строками (customer без БИН из
    # листинга + organizer с БИН из деталей) — показываем лоты всех её записей.
    org_ids = related_org_ids(db, org)
    org_lots_cond = or_(
        Lot.customer_id.in_(org_ids),
        Announcement.organizer_id.in_(org_ids),
    )
    lots = db.scalars(
        select(Lot)
        .outerjoin(Announcement, Announcement.id == Lot.announcement_id)
        .where(org_lots_cond)
        .order_by(desc(Lot.first_seen))
        .options(selectinload(Lot.customer))
    ).all()
    actual = [lt for lt in lots if lt.is_actual]
    past = [lt for lt in lots if not lt.is_actual]
    total_plan = sum((lt.plan_amount or 0) for lt in lots)
    # Покрытие данных: сколько лотов по годам публикации уже в БД и когда
    # последний раз синхронизировались — видно, нужна ли дозагрузка /ingest.
    yr = func.extract("year", Announcement.publish_date)
    year_counts = db.execute(
        select(yr.label("yr"), func.count(Lot.id).label("n"))
        .select_from(Lot)
        .join(Announcement, Announcement.id == Lot.announcement_id)
        .where(org_lots_cond, Announcement.publish_date.is_not(None))
        .group_by(yr)
        .order_by(yr)
    ).all()
    last_synced = max((lt.last_synced for lt in lots), default=None)
    n_winner = sum(1 for lt in lots if lt.winner_bin)
    return templates.TemplateResponse(
        request,
        "organization.html",
        {
            **_base_ctx(request, db, user),
            "org": org,
            "actual": actual,
            "past": past,
            "lots": lots,
            "total_plan": total_plan,
            "year_counts": year_counts,
            "last_synced": last_synced,
            "n_winner": n_winner,
        },
    )


@app.get("/organization/{org_id}/report", response_class=HTMLResponse)
def organization_report(
    org_id: int,
    request: Request,
    format: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Отчёт по закупкам организации: агрегаты по уже загруженным лотам.

    Только SQL по локальной БД — к goszakup не ходит. Для полноты картины
    лоты организации сначала догружаются через /ingest по БИН. Admin-only:
    отчёт агрегирует все данные без persona-scope (правило #15)."""
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "не найдено")
    report = build_org_report(db, org)
    if format == "md":
        md = render_markdown(report, base_url=PUBLIC_BASE_URL)
        fname = f"report_{org.bin or org.id}.md"
        return PlainTextResponse(
            md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
    return templates.TemplateResponse(
        request,
        "org_report.html",
        {**_base_ctx(request, db, user), **report},
    )


@app.get("/suppliers", response_class=HTMLResponse)
def suppliers_report(
    request: Request,
    enstru_code: str = "",
    enstru: str = "",
    category: str = "",
    year: int = 0,
    format: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Отчёт по поставщикам для лидогенерации: кто выигрывает/проигрывает
    по кодам ЕНС ТРУ + контакты из реестра участников (правило #22,
    jobs/supplier_report.py). Чистый SQL по БД, admin-only — контакты и
    агрегаты без persona-scope."""
    f = SupplierFilters(
        enstru_code=enstru_code.strip(),
        enstru=enstru.strip(),
        category=category.strip(),
        year=year,
    )
    rows = build_supplier_report(db, f)
    if format == "csv":
        # BOM — чтобы Excel открывал UTF-8 без кракозябр.
        return PlainTextResponse(
            "\ufeff" + render_suppliers_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="suppliers.csv"'},
        )
    years = [
        int(y)
        for y in db.scalars(
            select(func.extract("year", Announcement.publish_date))
            .where(Announcement.publish_date.is_not(None))
            .distinct()
            .order_by(func.extract("year", Announcement.publish_date).desc())
        )
    ]
    with_contacts = sum(1 for r in rows if r.phone or r.email)
    return templates.TemplateResponse(
        request,
        "suppliers.html",
        {
            **_base_ctx(request, db, user),
            "rows": rows,
            "f": f,
            "verticals": CATEGORY_CHOICES,
            "years": years,
            "with_contacts": with_contacts,
        },
    )


@app.get("/presets", response_class=HTMLResponse)
def presets_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    presets = db.scalars(select(Preset).order_by(Preset.id)).all()
    return templates.TemplateResponse(
        request,
        "presets.html",
        {**_base_ctx(request, db, user), "presets": presets, "region_lookup": BY_CODE},
    )


@app.post("/presets/{preset_id}/toggle")
def preset_toggle(
    preset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    p = db.get(Preset, preset_id)
    if not p:
        raise HTTPException(404)
    p.active = not p.active
    db.commit()
    return RedirectResponse("/presets", status_code=303)


# === Семантические запросы пользователя (UserQuery) и матчи ===


def _trigger_backfill(query_id: int) -> int | None:
    """Запустить backfill по запросу. Не валим UI, если брокер/Redis недоступен —
    forward-матчинг новых лотов всё равно сработает, а backfill можно вызвать
    позже командой `python -m goszakup match-backfill`.

    Возвращает число лотов, поставленных в очередь, или None если брокер
    недоступен (тогда UI покажет предупреждение вместо «поставлено N»)."""
    try:
        from ..jobs.match import backfill_query

        return backfill_query(query_id)
    except Exception as e:  # noqa: BLE001 — UI важнее, чем мгновенный backfill
        import logging

        logging.getLogger(__name__).warning(
            "backfill для query %s не запущен (брокер недоступен?): %s", query_id, e
        )
        return None


@app.get("/queries", response_class=HTMLResponse)
def queries_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("queries")),
):
    queries = db.scalars(
        select(UserQuery)
        .where(UserQuery.user_id == user.id)
        .order_by(UserQuery.id)
    ).all()
    # Счётчик матчей по каждому запросу — для наглядности.
    counts = {
        q.id: db.scalar(
            select(func.count(UserLotMatch.id)).where(
                UserLotMatch.user_query_id == q.id,
                UserLotMatch.matched.is_(True),
            )
        ) or 0
        for q in queries
    }
    return templates.TemplateResponse(
        request,
        "queries.html",
        {**_base_ctx(request, db, user), "queries": queries, "match_counts": counts},
    )


@app.post("/queries")
def query_create(
    name: str = Form(...),
    text: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_page("queries")),
):
    name = name.strip() or "Без названия"
    text = text.strip()
    if not text:
        return RedirectResponse("/queries", status_code=303)
    q = UserQuery(user_id=user.id, name=name, text=text)
    db.add(q)
    db.commit()
    _trigger_backfill(q.id)
    return RedirectResponse("/queries", status_code=303)


def _owned_query(db: Session, query_id: int, user: User) -> UserQuery:
    q = db.get(UserQuery, query_id)
    if q is None or q.user_id != user.id:
        raise HTTPException(404)
    return q


@app.post("/queries/{query_id}/edit")
def query_edit(
    query_id: int,
    name: str = Form(...),
    text: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_page("queries")),
):
    q = _owned_query(db, query_id, user)
    new_text = text.strip()
    q.name = name.strip() or q.name
    # Правка текста инвалидирует кеш матчей — поднимаем version и пере-backfill'им.
    if new_text and new_text != q.text:
        q.text = new_text
        q.version += 1
        db.commit()
        _trigger_backfill(q.id)
    else:
        db.commit()
    return RedirectResponse("/queries", status_code=303)


@app.post("/queries/{query_id}/rematch")
def query_rematch(
    query_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("queries")),
):
    # Ручной повтор backfill'а: прогнать запрос по актуальным лотам в scope
    # прямо сейчас, не дожидаясь ночного forward-матчинга новых лотов.
    q = _owned_query(db, query_id, user)
    n = _trigger_backfill(q.id)
    if n is None:
        return RedirectResponse(f"/queries?rematch_failed={q.id}", status_code=303)
    return RedirectResponse(f"/queries?rematched={q.id}&queued={n}", status_code=303)


@app.post("/queries/{query_id}/toggle")
def query_toggle(
    query_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("queries")),
):
    q = _owned_query(db, query_id, user)
    q.active = not q.active
    db.commit()
    return RedirectResponse("/queries", status_code=303)


@app.post("/queries/{query_id}/delete")
def query_delete(
    query_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("queries")),
):
    q = _owned_query(db, query_id, user)
    db.delete(q)  # cascade удалит UserLotMatch
    db.commit()
    return RedirectResponse("/queries", status_code=303)


@app.get("/matched", response_class=HTMLResponse)
def matched_page(
    request: Request,
    query_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("matched")),
):
    """Подходящие лоты — чистый SQL по кешу UserLotMatch, без LLM."""
    # Выключенные запросы (active=False) в подбор не попадают: ни в фильтр,
    # ни в выборку матчей.
    queries = db.scalars(
        select(UserQuery)
        .where(UserQuery.user_id == user.id, UserQuery.active.is_(True))
        .order_by(UserQuery.id)
    ).all()
    query_ids = [q.id for q in queries]

    rows = []
    if query_ids:
        match_filter = [
            UserLotMatch.user_query_id.in_(query_ids),
            UserLotMatch.matched.is_(True),
            Lot.is_actual.is_(True),
        ]
        if query_id is not None:
            match_filter.append(UserLotMatch.user_query_id == query_id)

        # Список — про лоты, а не про пары: лот под несколькими запросами
        # показываем один раз с лучшим баллом. Раньше тянули ВСЕ матчи с
        # eager-load и резали в Python — на N запросов × 800K лотов это full
        # result set на каждый заход. Теперь двухфазно с LIMIT в SQL:
        # Фаза 1 — top-N РАЗНЫХ лотов по лучшему баллу (без eager-load).
        best = (
            select(UserLotMatch.lot_id, func.max(UserLotMatch.score).label("best"))
            .join(Lot, UserLotMatch.lot_id == Lot.id)
            .where(*match_filter)
            .group_by(UserLotMatch.lot_id)
            .order_by(desc("best"), UserLotMatch.lot_id)
            .limit(PAGE_SIZE)
        )
        lot_ids_ordered = [r.lot_id for r in db.execute(best).all()]

        # Фаза 2 — сами лоты + лучший матч на лот; eager-load только для ≤PAGE_SIZE.
        if lot_ids_ordered:
            pair_stmt = (
                select(UserLotMatch, Lot, UserQuery)
                .join(Lot, UserLotMatch.lot_id == Lot.id)
                .join(UserQuery, UserLotMatch.user_query_id == UserQuery.id)
                .options(selectinload(Lot.customer), selectinload(Lot.announcement))
                .where(*match_filter, UserLotMatch.lot_id.in_(lot_ids_ordered))
                .order_by(desc(UserLotMatch.score), desc(UserLotMatch.matched_at))
            )
            best_by_lot: dict[int, tuple] = {}
            for m, lot, q in db.execute(pair_stmt).all():
                best_by_lot.setdefault(lot.id, (m, lot, q))
            rows = [best_by_lot[lid] for lid in lot_ids_ordered if lid in best_by_lot]

    return templates.TemplateResponse(
        request,
        "matched.html",
        {
            **_base_ctx(request, db, user),
            "queries": queries,
            "rows": rows,
            "active_query_id": query_id,
        },
    )


@app.get("/runs", response_class=HTMLResponse)
def runs_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    close_stale_runs(db)  # чтобы в списке зависшие прогоны были с финишем
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
        request, "runs.html", {**_base_ctx(request, db, user), "runs": runs}
    )


@app.get("/submissions", response_class=HTMLResponse)
def submissions_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Список автоподач со статусом и латентностью «выстрела» (правило #19).

    Цену НЕ показываем — она зашифрована (sealed-bid). Только лоты/статус/тайминг.
    """
    rows = db.execute(
        select(Submission, ClientCredential.label)
        .join(ClientCredential, ClientCredential.id == Submission.credential_id)
        .order_by(desc(Submission.open_at))
        .limit(200)
    ).all()
    subs = [
        {
            "id": s.id,
            "client": label,
            "anno_id": s.anno_id,
            "anno_number": s.anno_number,
            "lot_ids": s.lot_ids or [],
            "status": s.status,
            "open_at": s.open_at,
            "close_at": s.close_at,
            "fired_at": s.fired_at,
            "fire_latency_ms": s.fire_latency_ms,
            "agent_node": s.agent_node,
            "attempts": s.attempts,
            "error": s.error,
        }
        for s, label in rows
    ]
    return templates.TemplateResponse(
        request, "submissions.html", {**_base_ctx(request, db, user), "subs": subs}
    )


@app.post("/autosubmit/result")
async def autosubmit_result(request: Request, db: Session = Depends(get_db)):
    """Ingest RunResult от Windows submit-agent. Машинная авторизация по токену.

    Не cookie-сессия (агент — не залогиненный пользователь). Сравнение токена —
    constant-time. Без GZ_AUTOSUBMIT_INGEST_TOKEN ingest выключен.
    """
    if not AUTOSUBMIT_INGEST_TOKEN:
        return JSONResponse({"error": "ingest disabled"}, status_code=503)
    sent = request.headers.get("X-Autosubmit-Token", "")
    if not hmac.compare_digest(sent, AUTOSUBMIT_INGEST_TOKEN):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
        result = RunResult.from_dict(payload)
    except (ValueError, TypeError, KeyError) as e:
        return JSONResponse({"error": f"bad payload: {e}"}, status_code=400)

    sub = apply_result(db, result)
    if sub is None:
        return JSONResponse({"error": "submission not found"}, status_code=404)
    return JSONResponse({"ok": True, "submission_id": sub.id, "status": sub.status})


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Callback'и inline-кнопок Telegram (кнопка «Подробнее» в уведомлении).

    Машинная авторизация: Telegram шлёт secret_token из setWebhook в заголовке
    X-Telegram-Bot-Api-Secret-Token. Ответ пользователю готовит explain_actor
    (LLM — десятки секунд, вебхук должен вернуть 200 быстро). На нерелевантные
    апдейты отвечаем 200 — иначе Telegram будет ретраить их бесконечно.
    """
    if not TELEGRAM_WEBHOOK_SECRET:
        return JSONResponse({"error": "webhook disabled"}, status_code=503)
    sent = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(sent, TELEGRAM_WEBHOOK_SECRET):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    from ..notify.telegram import answer_callback_query, send_placeholder
    from ..queue.notify import explain_actor

    try:
        update = await request.json()
    except ValueError:
        return JSONResponse({"ok": True})

    cq = update.get("callback_query") if isinstance(update, dict) else None
    if not cq:
        return JSONResponse({"ok": True})

    data = cq.get("data") or ""
    chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
    if data.startswith("explain:") and chat_id is not None:
        try:
            lot_id = int(data.split(":", 1)[1])
        except ValueError:
            lot_id = None
        if lot_id is not None:
            # answer_callback_query/send_placeholder/explain_actor.send —
            # синхронный блокирующий httpx/Redis I/O. В async-роуте вызывать их
            # напрямую нельзя: блокируют event loop uvicorn на каждый клик
            # кнопки. Оффлоадим в threadpool (Гейт 2).
            await run_in_threadpool(answer_callback_query, cq.get("id", ""))
            # Заглушка в чат сразу: LLM думает десятки секунд, а тост от
            # answerCallbackQuery живёт пару секунд — пользователь должен
            # видеть, что процесс идёт. Актор потом отредактирует её в ответ.
            placeholder_id = await run_in_threadpool(
                send_placeholder,
                str(chat_id),
                "⏳ Готовлю объяснение лота — обычно занимает до минуты…",
            )
            await run_in_threadpool(explain_actor.send, lot_id, str(chat_id), placeholder_id)
    return JSONResponse({"ok": True})


_LLM_KIND_LABELS = {"analyze": "Анализ ТЗ", "match": "Подбор", "chat": "Чат", "explain": "Объяснение (TG)"}


def _llm_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Оценка стоимости в USD по тарифу из config (см. LLM_PRICE_*)."""
    return (
        prompt_tokens / 1_000_000 * LLM_PRICE_INPUT_PER_MTOK
        + completion_tokens / 1_000_000 * LLM_PRICE_OUTPUT_PER_MTOK
    )


def _llm_day_expr(dialect: str):
    """SQL-выражение «UTC-день» строкой YYYY-MM-DD для GROUP BY (кросс-диалектно)."""
    if dialect == "postgresql":
        # created_at — timestamptz(UTC); AT TIME ZONE 'UTC' даёт wall-clock UTC,
        # чтобы граница суток не плавала по таймзоне сессии.
        return func.to_char(LlmCall.created_at.op("AT TIME ZONE")("UTC"), "YYYY-MM-DD")
    return func.strftime("%Y-%m-%d", LlmCall.created_at)


def _rollup_days(daily: list[dict], key_fn, label_fn, limit: int) -> list[dict]:
    """Свернуть уже суточно-агрегированные строки в недели/месяцы (в Python —
    их ≤400, дёшево). Возвращает последние `limit` периодов, свежие сверху."""
    buckets: dict = {}
    for d in daily:
        key = key_fn(d["date"])
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {
                "key": key,
                "label": label_fn(d["date"]),
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        b["calls"] += d["calls"]
        b["prompt_tokens"] += d["prompt_tokens"]
        b["completion_tokens"] += d["completion_tokens"]
        b["total_tokens"] += d["total_tokens"]
    out = sorted(buckets.values(), key=lambda b: b["key"], reverse=True)[:limit]
    for b in out:
        b["cost"] = _llm_cost(b["prompt_tokens"], b["completion_tokens"])
    return out


@app.get("/expenses", response_class=HTMLResponse)
def expenses(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # Окно ~13 месяцев покрывает «по месяцам за год». Агрегируем в SQL: суточный
    # GROUP BY сжимает 800K строк до ≤400 суток (по индексу created_at), а недели
    # и месяцы дешёво сворачиваем из суток в Python. Раньше тянули ВСЕ строки в
    # память на каждый заход админа.
    since = datetime.now(UTC) - timedelta(days=400)
    day_col = _llm_day_expr(db.bind.dialect.name)
    day_rows = db.execute(
        select(
            day_col.label("day"),
            func.count().label("calls"),
            func.coalesce(func.sum(LlmCall.prompt_tokens), 0),
            func.coalesce(func.sum(LlmCall.completion_tokens), 0),
            func.coalesce(func.sum(LlmCall.total_tokens), 0),
        )
        .where(LlmCall.created_at >= since)
        .group_by(day_col)
    ).all()
    daily = [
        {
            "date": date.fromisoformat(r[0]),
            "calls": r[1],
            "prompt_tokens": r[2],
            "completion_tokens": r[3],
            "total_tokens": r[4],
        }
        for r in day_rows
    ]

    by_day = _rollup_days(daily, lambda d: d.isoformat(), lambda d: d.isoformat(), 30)
    by_week = _rollup_days(
        daily,
        lambda d: "{:04d}-W{:02d}".format(*d.isocalendar()[:2]),
        lambda d: "{:04d} · неделя {:02d}".format(*d.isocalendar()[:2]),
        12,
    )
    by_month = _rollup_days(
        daily, lambda d: d.strftime("%Y-%m"), lambda d: d.strftime("%Y-%m"), 12
    )

    # Итоги по типам вызова за всё окно — тоже GROUP BY в SQL.
    kind_rows = db.execute(
        select(
            LlmCall.kind,
            func.count(),
            func.coalesce(func.sum(LlmCall.prompt_tokens), 0),
            func.coalesce(func.sum(LlmCall.completion_tokens), 0),
            func.coalesce(func.sum(LlmCall.total_tokens), 0),
        )
        .where(LlmCall.created_at >= since)
        .group_by(LlmCall.kind)
    ).all()
    by_kind: dict = {}
    grand = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for kind, calls, pt, ct, tt in kind_rows:
        by_kind[kind] = {
            "label": _LLM_KIND_LABELS.get(kind, kind),
            "calls": calls,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
            "cost": _llm_cost(pt, ct),
        }
        grand["calls"] += calls
        grand["prompt_tokens"] += pt
        grand["completion_tokens"] += ct
        grand["total_tokens"] += tt
    grand["cost"] = _llm_cost(grand["prompt_tokens"], grand["completion_tokens"])

    return templates.TemplateResponse(
        request,
        "expenses.html",
        {
            **_base_ctx(request, db, user),
            "by_day": by_day,
            "by_week": by_week,
            "by_month": by_month,
            "by_kind": sorted(by_kind.values(), key=lambda x: -x["total_tokens"]),
            "grand": grand,
            "price_in": LLM_PRICE_INPUT_PER_MTOK,
            "price_out": LLM_PRICE_OUTPUT_PER_MTOK,
            "window_days": 400,
        },
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
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
            **_base_ctx(request, db, user),
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
    user: User = Depends(require_page("ingest")),
    customer_bin: str = "",
    error: str = "",
):
    current_year = datetime.now(UTC).year
    active = find_active_run(db)
    return templates.TemplateResponse(
        request,
        "ingest.html",
        {
            **_base_ctx(request, db, user),
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
    user: User = Depends(require_page("ingest")),
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


# === /scan — ad-hoc прогон по kato/amount/status/вертикалям ===


_SCAN_MODES = [
    (MODE_LISTING, "Только листинг", "Быстрый разведочный проход — без HTTP-нагрузки на детали."),
    (MODE_NO_HEAVY, "Листинг + детали", "Тянет organizer, контакты и договоры. Без документов и LLM."),
    (MODE_FULL, "Полный (как daily)", "Детали + документы + LLM-анализ — только для watchlist-лотов."),
]


@app.get("/scan", response_class=HTMLResponse)
def scan_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    error: str = "",
):
    active = find_active_run(db)
    return templates.TemplateResponse(
        request,
        "scan.html",
        {
            **_base_ctx(request, db, user),
            "regions": REGIONS,
            "status_groups": _STATUS_GROUPS,
            "verticals": CATEGORY_CHOICES,
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
    category: list[str] = Form(default=[]),
    mode: str = Form(MODE_FULL),
    user: User = Depends(require_admin),
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
    # Чекбоксы вертикалей приходят строками — отфильтруем мусор.
    cat_clean = [c for c in (category or []) if c in CATEGORY_SLUGS]
    status_clean = list(status or [])

    try:
        run_id = create_scan_run(
            kato=kato,
            amount_from=amount_from,
            amount_to=amount_to_int,
            status_codes=status_clean,
            categories=cat_clean,
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
        cat_clean,
        listing_only,
        with_docs,
        with_llm,
    )
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


# === Аутентификация и управление пользователями ===


def _safe_next(next_url: str) -> str:
    """Только локальные пути — иначе open-redirect через ?next=//evil.com."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db), next: str = "", error: str = ""):
    # Уже вошёл — незачем показывать форму.
    from .auth import get_current_user

    if get_current_user(request, db) is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"version": __version__, "next": next, "error": error},
    )


@app.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    user = authenticate(db, username.strip(), password)
    if user is None:
        qs = urlencode({"error": "1", "next": next} if next else {"error": "1"})
        return RedirectResponse(f"/login?{qs}", status_code=303)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    request.session["uid"] = user.id
    return RedirectResponse(_safe_next(next), status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("settings")),
    ok: str = "",
    error: str = "",
):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            **_base_ctx(request, db, user),
            # Без токена бот не настроен на сервере — покажем предупреждение,
            # чтобы пользователь не думал, что у него что-то сломалось.
            "bot_configured": bool(GZ_TELEGRAM_BOT_TOKEN),
            "ok": ok,
            "error": error,
        },
    )


@app.post("/settings")
def settings_save(
    request: Request,
    telegram_chat_id: str = Form(""),
    notify_telegram: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_page("settings")),
):
    chat_id = telegram_chat_id.strip()
    user.telegram_chat_id = chat_id or None
    # Включать рассылку без chat_id бессмысленно — гасим тумблер.
    user.notify_telegram = bool(notify_telegram) and bool(chat_id)
    db.commit()
    return RedirectResponse("/settings?ok=1", status_code=303)


@app.post("/settings/test")
def settings_test(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_page("settings")),
):
    from ..notify.telegram import send_message

    if not user.telegram_chat_id:
        return RedirectResponse("/settings?error=no_chat_id", status_code=303)
    ok, err = send_message(
        user.telegram_chat_id,
        "✅ Тестовое сообщение от трекера тендеров. Уведомления подключены.",
    )
    if ok:
        return RedirectResponse("/settings?ok=test", status_code=303)
    return RedirectResponse(
        f"/settings?error={quote(err or 'unknown')}", status_code=303
    )


def _parse_scope_form(
    regions: list[str], categories: list[str], min_amount: str
) -> dict:
    return {
        "regions": [r for r in regions if r in BY_CODE] or None,
        "categories": [c for c in categories if c in CATEGORY_SLUGS] or None,
        "min_amount": _maybe_int(min_amount),
    }


def _role_id_or_none(db: Session, raw: str) -> int | None:
    """'' или несуществующий id из формы → None (без роли = все вкладки)."""
    rid = _maybe_int(raw)
    if rid is None:
        return None
    return rid if db.get(Role, rid) is not None else None


@app.get("/users", response_class=HTMLResponse)
def users_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    error: str = "",
    ok: str = "",
):
    users = db.scalars(select(User).order_by(User.id)).all()
    roles = db.scalars(select(Role).order_by(Role.name)).all()
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            **_base_ctx(request, db, user),
            "users": users,
            "roles": roles,
            "regions": REGIONS,
            "verticals": CATEGORY_CHOICES,
            "error": error,
            "ok": ok,
        },
    )


@app.post("/users")
def users_create(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    username: str = Form(...),
    password: str = Form(...),
    is_admin: str = Form(""),
    role_id: str = Form(""),
    regions: list[str] = Form(default=[]),
    categories: list[str] = Form(default=[]),
    min_amount: str = Form(""),
):
    username = username.strip()
    if not username or len(password) < 6:
        return RedirectResponse("/users?error=invalid", status_code=303)
    if db.scalar(select(User).where(User.username == username)) is not None:
        return RedirectResponse("/users?error=exists", status_code=303)
    scope = _parse_scope_form(regions, categories, min_amount)
    db.add(
        User(
            username=username,
            password_hash=hash_password(password),
            is_admin=(is_admin == "1"),
            role_id=_role_id_or_none(db, role_id),
            is_active=True,
            **scope,
        )
    )
    db.commit()
    _bust_nav_cache()
    return RedirectResponse("/users?ok=created", status_code=303)


@app.post("/users/{user_id}/edit")
def users_edit(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    password: str = Form(""),
    is_admin: str = Form(""),
    is_active: str = Form(""),
    role_id: str = Form(""),
    regions: list[str] = Form(default=[]),
    categories: list[str] = Form(default=[]),
    min_amount: str = Form(""),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "пользователь не найден")
    scope = _parse_scope_form(regions, categories, min_amount)
    target.regions = scope["regions"]
    target.categories = scope["categories"]
    target.min_amount = scope["min_amount"]
    target.role_id = _role_id_or_none(db, role_id)
    # Себя нельзя разжаловать/деактивировать — иначе можно потерять доступ.
    if target.id != user.id:
        target.is_admin = is_admin == "1"
        target.is_active = is_active == "1"
    if password:
        if len(password) < 6:
            return RedirectResponse("/users?error=invalid", status_code=303)
        target.password_hash = hash_password(password)
    db.commit()
    _bust_nav_cache()
    return RedirectResponse("/users?ok=saved", status_code=303)


@app.post("/users/{user_id}/delete")
def users_delete(
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if user_id == user.id:
        return RedirectResponse("/users?error=self_delete", status_code=303)
    target = db.get(User, user_id)
    if target is not None:
        # Сначала запросы пользователя — иначе на Postgres удаление упадёт по
        # FK user_queries.user_id; матчи каскадно удалит ORM (delete-orphan).
        for q in db.scalars(select(UserQuery).where(UserQuery.user_id == user_id)):
            db.delete(q)
        db.delete(target)
        db.commit()
    return RedirectResponse("/users?ok=deleted", status_code=303)


def _parse_role_form(name: str, pages: list[str]) -> tuple[str, list[str]] | None:
    name = name.strip()
    if not name:
        return None
    # Сохраняем в порядке реестра — чтобы список в UI был стабильным.
    keys = [key for key, _, _ in ALL_PAGES if key in set(pages) & ALL_KEYS]
    return name, keys


@app.get("/roles", response_class=HTMLResponse)
def roles_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    error: str = "",
    ok: str = "",
):
    roles = db.scalars(select(Role).order_by(Role.id)).all()
    user_counts = dict(
        db.execute(
            select(User.role_id, func.count(User.id))
            .where(User.role_id.is_not(None))
            .group_by(User.role_id)
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "roles.html",
        {
            **_base_ctx(request, db, user),
            "roles": roles,
            "user_counts": user_counts,
            "pages": PAGES,
            "system_pages": SYSTEM_PAGES,
            "error": error,
            "ok": ok,
        },
    )


@app.post("/roles")
def roles_create(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    name: str = Form(...),
    pages: list[str] = Form(default=[]),
):
    parsed = _parse_role_form(name, pages)
    if parsed is None:
        return RedirectResponse("/roles?error=invalid", status_code=303)
    name, keys = parsed
    if db.scalar(select(Role).where(Role.name == name)) is not None:
        return RedirectResponse("/roles?error=exists", status_code=303)
    db.add(Role(name=name, pages=keys))
    db.commit()
    return RedirectResponse("/roles?ok=created", status_code=303)


@app.post("/roles/{role_id}/edit")
def roles_edit(
    role_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
    name: str = Form(...),
    pages: list[str] = Form(default=[]),
):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "роль не найдена")
    parsed = _parse_role_form(name, pages)
    if parsed is None:
        return RedirectResponse("/roles?error=invalid", status_code=303)
    new_name, keys = parsed
    if new_name != role.name and db.scalar(select(Role).where(Role.name == new_name)):
        return RedirectResponse("/roles?error=exists", status_code=303)
    role.name = new_name
    role.pages = keys
    db.commit()
    return RedirectResponse("/roles?ok=saved", status_code=303)


@app.post("/roles/{role_id}/delete")
def roles_delete(
    role_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    role = db.get(Role, role_id)
    if role is None:
        return RedirectResponse("/roles", status_code=303)
    # Удаление назначенной роли молча открыло бы её пользователям все вкладки
    # (NULL = полный доступ) — заставляем сначала переназначить пользователей.
    assigned = db.scalar(select(func.count(User.id)).where(User.role_id == role_id)) or 0
    if assigned:
        return RedirectResponse("/roles?error=in_use", status_code=303)
    db.delete(role)
    db.commit()
    return RedirectResponse("/roles?ok=deleted", status_code=303)
