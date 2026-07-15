"""SQLAlchemy 2.0 модели.

Типы выбраны под оба диалекта:
- Деньги — `Numeric(18, 2)` (Decimal в Python), а не Float. Float = IEEE-754
  double, теоретически точен до ~15 цифр, но «учётно неправильный» для денег
  и в Postgres использует `double precision`, а не `numeric`. Decimal даёт
  бит-в-бит сохранение того, что отдал goszakup.
- JSON-поля — `JSON().with_variant(JSONB, "postgresql")`. На SQLite остаётся
  text-JSON; на Postgres становится `jsonb` (поддерживает GIN-индексы и
  частичные обновления).
- Временные метки — `DateTime(timezone=True)` (TIMESTAMPTZ на Postgres,
  ISO+offset на SQLite). `_now()` возвращает aware datetime в UTC, чтобы
  не было mixed-tz сравнений в Python.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    """Aware UTC datetime — стыкуется с `DateTime(timezone=True)`."""
    return datetime.now(UTC)


# Деньги в тенге. Тендеры на goszakup бывают и в копейках (тиынах), поэтому
# 2 знака после запятой — не пустая трата места, а сохранение исходного
# значения. `precision=18` спокойно хватает на тендеры до ~10 трлн ₸.
MONEY_TYPE = Numeric(precision=18, scale=2)

# JSON, который на Postgres превращается в jsonb. JSON()-вариант (для SQLite)
# хранит сериализованный текст. JSONB-вариант — нативно с поддержкой GIN,
# `?`/`->`/`->>` операторов, частичных обновлений. Для нас критично, когда
# на 10М+ лотов понадобится индекс по `lot_analyses.tech_stack` или
# `announcements.raw`.
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")

# `DateTime(timezone=True)` — TIMESTAMPTZ. Постгрес хранит как UTC, отдаёт
# в session-tz. Для нас сессия — UTC (см. docker-compose; внутри контейнера
# таймзоной никто не управляет — psycopg по умолчанию UTC).
TS_TYPE = DateTime(timezone=True)


class User(Base):
    """Учётная запись для входа в UI.

    Multi-tenant поверх общих данных: лоты в БД одни на всех (скрейпинг
    глобальный), но у каждого пользователя свой read-time `scope` —
    регионы (kato), IT-категории и минимальная сумма. `/actual`, `/past`,
    `/starred`, дашборд и счётчики сайдбара показывают только лоты в его
    scope; админ (`is_admin`) видит всё. Пустой `regions`/`it_categories`
    (NULL или []) = «без ограничения по этому измерению».
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    # bcrypt-хеш (60 символов). Не Basic Auth из env — пароли в БД.
    password_hash: Mapped[str] = mapped_column(String(100))
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )
    # scope: список kato-кодов и IT-категорий; min_amount перекрывает
    # глобальный MIN_AMOUNT для этого пользователя.
    regions: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    it_categories: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    min_amount: Mapped[int | None] = mapped_column(Integer)
    # Telegram chat_id (numeric, в виде строки) для уведомлений о новых
    # подходящих лотах. Пустой → уведомления не шлём. `notify_telegram` —
    # тумблер: можно сохранить chat_id, но временно выключить рассылку.
    telegram_chat_id: Mapped[str | None] = mapped_column(String(40))
    notify_telegram: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(TS_TYPE)


class Organization(Base):
    """Заказчик / организатор / поставщик. Одно лицо может быть во всех ролях."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bin: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(500), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(200))
    first_seen: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    last_seen: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, onupdate=_now)

    organized_announcements: Mapped[list[Announcement]] = relationship(
        back_populates="organizer", foreign_keys="Announcement.organizer_id"
    )
    customer_lots: Mapped[list[Lot]] = relationship(
        back_populates="customer", foreign_keys="Lot.customer_id"
    )


class Announcement(Base):
    """Объявление о закупке (один или несколько лотов внутри)."""

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # anno_id с сайта
    number: Mapped[str | None] = mapped_column(String(50), index=True)
    url: Mapped[str] = mapped_column(String(300))
    method: Mapped[str | None] = mapped_column(String(200))
    purchase_type: Mapped[str | None] = mapped_column(String(100))
    subject_type: Mapped[str | None] = mapped_column(String(50))
    organizer_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    organizer_address: Mapped[str | None] = mapped_column(Text)
    lots_count: Mapped[int | None] = mapped_column(Integer)
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY_TYPE)
    attributes: Mapped[str | None] = mapped_column(Text)  # «Признаки»
    publish_date: Mapped[datetime | None] = mapped_column(TS_TYPE)
    # Дата и время окончания приёма заявок (UTC). По нему крон снимает лоты
    # с «актуальных» (is_actual=False), когда срок прошёл, даже если goszakup
    # ещё не сменил статус. См. jobs/expire.py. index — для дешёвого
    # bulk-UPDATE по `application_end < now()`.
    application_end: Mapped[datetime | None] = mapped_column(TS_TYPE, index=True)
    contact_name: Mapped[str | None] = mapped_column(String(300))
    contact_role: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(200))
    raw: Mapped[dict | None] = mapped_column(JSON_TYPE)
    first_seen: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    last_synced: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, onupdate=_now)

    organizer: Mapped[Organization | None] = relationship(
        back_populates="organized_announcements", foreign_keys=[organizer_id]
    )
    lots: Mapped[list[Lot]] = relationship(back_populates="announcement")
    documents: Mapped[list[Document]] = relationship(back_populates="announcement")


class Lot(Base):
    """Лот внутри объявления."""

    __tablename__ = "lots"
    __table_args__ = (
        # /actual, /past: WHERE is_actual=? ORDER BY first_seen DESC — составной
        # индекс закрывает и фильтр, и сортировку одним проходом (на 800K строк
        # одноколоночные is_actual+first_seen дают bitmap-and + отдельный Sort).
        Index("ix_lots_actual_first_seen", "is_actual", "first_seen"),
        # /runs/{id}: WHERE last_synced BETWEEN started_at AND upper — раньше
        # last_synced не был индексирован вообще (в отличие от first_seen).
        Index("ix_lots_last_synced", "last_synced"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # lot_id с сайта
    number: Mapped[str | None] = mapped_column(String(50), index=True)
    announcement_id: Mapped[int | None] = mapped_column(
        ForeignKey("announcements.id"), index=True
    )
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    enstru: Mapped[str | None] = mapped_column(String(500), index=True)
    # Цифровой код ЕНС ТРУ (напр. 192021.530.000001). В отличие от `enstru`
    # (наименование из листинга), есть только на карточке лота — тянется в
    # фазе 2 для IT-лотов. Индекс — под будущую фильтрацию выборок по коду.
    enstru_code: Mapped[str | None] = mapped_column(String(40), index=True)
    name: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[str | None] = mapped_column(Text)  # доп. характеристика
    price_per_unit: Mapped[Decimal | None] = mapped_column(MONEY_TYPE)
    # Кол-во — не деньги, может быть дробным (литры, тонны, штуки.5). Float OK.
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(100))
    plan_amount: Mapped[Decimal | None] = mapped_column(MONEY_TYPE, index=True)
    amount_y1: Mapped[Decimal | None] = mapped_column(MONEY_TYPE)
    amount_y2: Mapped[Decimal | None] = mapped_column(MONEY_TYPE)
    amount_y3: Mapped[Decimal | None] = mapped_column(MONEY_TYPE)
    status_code: Mapped[int | None] = mapped_column(Integer, index=True)
    status_name: Mapped[str | None] = mapped_column(String(200), index=True)
    is_actual: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Ручная пометка «интересный лот» из UI (звёздочка на карточке/в списке).
    # Не связана с автоматикой пайплайна — выставляется только пользователем.
    is_starred: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False, index=True
    )
    it_category: Mapped[str | None] = mapped_column(String(50), index=True)
    kato: Mapped[str | None] = mapped_column(String(20), index=True)
    method: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(300))
    first_seen: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, index=True)
    last_synced: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, onupdate=_now)

    announcement: Mapped[Announcement | None] = relationship(back_populates="lots")
    customer: Mapped[Organization | None] = relationship(
        back_populates="customer_lots", foreign_keys=[customer_id]
    )
    status_history: Mapped[list[LotStatusHistory]] = relationship(
        back_populates="lot", cascade="all, delete-orphan"
    )
    contracts: Mapped[list[Contract]] = relationship(back_populates="lot")
    analysis: Mapped[LotAnalysis | None] = relationship(
        back_populates="lot",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="LotAnalysis.lot_id",
    )


class LotStatusHistory(Base):
    __tablename__ = "lot_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    status_code: Mapped[int | None] = mapped_column(Integer)
    status_name: Mapped[str | None] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, index=True)

    lot: Mapped[Lot] = relationship(back_populates="status_history")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("announcement_id", "url", name="uq_doc_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(500))
    attribute: Mapped[str | None] = mapped_column(String(200))  # «Признак»
    url: Mapped[str] = mapped_column(String(800))
    local_path: Mapped[str | None] = mapped_column(String(800))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(200))
    downloaded_at: Mapped[datetime | None] = mapped_column(TS_TYPE)
    download_error: Mapped[str | None] = mapped_column(Text)
    # SimHash по извлечённому тексту ТЗ (не по байтам — разные кодировки PDF
    # одинакового текста дают разный sha256, но одинаковый text_simhash).
    # Используется для дедупликации шаблонных тендеров. Хранится как BIGINT
    # (signed); см. classify/simhash.to_signed64.
    text_simhash: Mapped[int | None] = mapped_column(BigInteger, index=True)

    announcement: Mapped[Announcement] = relationship(back_populates="documents")


class Contract(Base):
    """Договор, привязанный к лоту (появляется на финальных стадиях)."""

    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    contract_number: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str | None] = mapped_column(String(100))
    plan_amount: Mapped[Decimal | None] = mapped_column(MONEY_TYPE)
    contract_amount: Mapped[Decimal | None] = mapped_column(MONEY_TYPE)
    fact_amount: Mapped[Decimal | None] = mapped_column(MONEY_TYPE)
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    supplier_status: Mapped[str | None] = mapped_column(String(100))
    last_synced: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, onupdate=_now)

    lot: Mapped[Lot] = relationship(back_populates="contracts")
    supplier: Mapped[Organization | None] = relationship(foreign_keys=[supplier_id])


class Preset(Base):
    """Именованный набор фильтров для ежедневного запуска."""

    __tablename__ = "presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kato: Mapped[str | None] = mapped_column(String(20))
    amount_from: Mapped[int | None] = mapped_column(Integer)
    amount_to: Mapped[int | None] = mapped_column(Integer)
    status_codes: Mapped[list[int] | None] = mapped_column(JSON_TYPE)
    it_categories: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, onupdate=_now)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preset_id: Mapped[int | None] = mapped_column(ForeignKey("presets.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS_TYPE)
    # Heartbeat: момент последнего прогресса по прогону. Бьётся на каждом шаге
    # пайплайна (listing-проход, detail, инкременты счётчиков). По нему reaper
    # (jobs.ingest.close_stale_runs) закрывает зависшие прогоны — finished_at
    # завязан на эфемерный Redis-счётчик и теряется при рестарте redis/воркера.
    last_progress_at: Mapped[datetime | None] = mapped_column(
        TS_TYPE, default=_now, index=True
    )
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    details_fetched: Mapped[int] = mapped_column(Integer, default=0)
    new_lots: Mapped[int] = mapped_column(Integer, default=0)
    updated_lots: Mapped[int] = mapped_column(Integer, default=0)
    new_documents: Mapped[int] = mapped_column(Integer, default=0)
    llm_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    log_excerpt: Mapped[str | None] = mapped_column(Text)
    # Произвольная подпись для ad-hoc прогонов (preset_id=NULL): отображается
    # на /runs вместо имени preset'а. Например, «БИН 990440004229 · 2020–2026
    # · услуги · статус 360».
    note: Mapped[str | None] = mapped_column(Text)


class LotAnalysis(Base):
    """LLM-классификация лота: dev-категория, стек, краткое ТЗ, риски.

    1:1 к Lot. Идемпотентность через (analyzer_version, tz_sha256): если оба
    совпадают с текущими — переанализ пропускается.
    """

    __tablename__ = "lot_analyses"
    __table_args__ = (UniqueConstraint("lot_id", name="uq_analysis_lot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    dev_category: Mapped[str | None] = mapped_column(String(40), index=True)
    tech_stack: Mapped[list[str] | None] = mapped_column(JSON_TYPE)
    tz_summary: Mapped[str | None] = mapped_column(Text)
    solo_feasible: Mapped[bool | None] = mapped_column(Boolean)
    vendor_lock_risk: Mapped[str | None] = mapped_column(String(10), index=True)
    analysis_confidence: Mapped[str | None] = mapped_column(String(10), index=True)
    analyzed_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    analyzer_version: Mapped[str] = mapped_column(String(40))
    tz_sha256: Mapped[str | None] = mapped_column(String(64))
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"), index=True
    )
    # Если анализ получен дедупликацией по simhash — ссылка на лот-источник,
    # с которого скопированы dev_category/tech_stack/tz_summary/risk. LLM в
    # этом случае не вызывался. NULL = анализ сделан LLM (или правилами).
    reused_from_lot_id: Mapped[int | None] = mapped_column(
        ForeignKey("lots.id"), index=True
    )
    error: Mapped[str | None] = mapped_column(Text)

    lot: Mapped[Lot] = relationship(back_populates="analysis", foreign_keys=[lot_id])
    source_document: Mapped[Document | None] = relationship(
        foreign_keys=[source_document_id]
    )
    reused_from: Mapped[Lot | None] = relationship(foreign_keys=[reused_from_lot_id])


class UserQuery(Base):
    """Семантическая настройка пользователя — «какие лоты хочу видеть».

    NL-предпочтение поверх детерминированного User.scope (regions/it_categories/
    min_amount). Scope грубо отсекает лоты SQL'ом, UserQuery даёт тонкий
    смысловой матч через LLM (см. classify/matcher.py). Несколько именованных
    запросов на пользователя — как Preset, но персональный и read-time.

    `version` растёт при правке `text` — это инвалидирует кеш UserLotMatch
    (матчи со старым query_version игнорируются и пересчитываются).
    `compiled_filters` — опциональный структурный «компилят» запроса (фаза 2):
    {dev_category, max_amount, kato...} для до-LLM отсева. Пока NULL.
    """

    __tablename__ = "user_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    compiled_filters: Mapped[dict | None] = mapped_column(JSON_TYPE)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, onupdate=_now)

    user: Mapped[User] = relationship()
    matches: Mapped[list[UserLotMatch]] = relationship(
        back_populates="query", cascade="all, delete-orphan"
    )


class UserLotMatch(Base):
    """Кеш результата матчинга (UserQuery × Lot). Зеркалит LotAnalysis.

    Идемпотентность по (query_version, matcher_version): если оба совпадают с
    текущими — пересчёт пропускается. Матч считается ОДИН раз на пару и потом
    читается на UI чистым SQL — никаких LLM-вызовов на запросах интерфейса.
    """

    __tablename__ = "user_lot_matches"
    __table_args__ = (
        UniqueConstraint("user_query_id", "lot_id", name="uq_match_query_lot"),
        # /matched: WHERE user_query_id IN (...) AND matched=? ORDER BY score DESC —
        # составной индекс вместо bitmap-and по трём одноколоночным при росте
        # UserLotMatch (N запросов × 800K лотов).
        Index("ix_match_query_matched_score", "user_query_id", "matched", "score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_query_id: Mapped[int] = mapped_column(
        ForeignKey("user_queries.id"), index=True
    )
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    matched: Mapped[bool] = mapped_column(Boolean, index=True)
    # 0..100 — для сортировки «самое релевантное сверху» на /matched.
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    matched_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    matcher_version: Mapped[str] = mapped_column(String(40))
    query_version: Mapped[int] = mapped_column(Integer)
    # Момент отправки Telegram-уведомления об этом матче. NULL = ещё не слали.
    # Гарантирует «одно уведомление на пару (запрос, лот)» даже при
    # переанализе лота, который заново триггерит match_actor. См. queue/notify.py.
    notified_at: Mapped[datetime | None] = mapped_column(TS_TYPE)

    query: Mapped[UserQuery] = relationship(back_populates="matches")
    lot: Mapped[Lot] = relationship()


class LlmCall(Base):
    """Учёт каждого реального вызова Cerebras — для отчёта о расходах.

    Одна строка = один HTTP-вызов к модели (анализ ТЗ, матч запроса, чат по
    лоту). Дедупликация/идемпотентность LotAnalysis/UserLotMatch сюда НЕ
    попадают: если LLM не дёргался (скип по версии, копия по simhash, rule-based)
    — строки нет. `kind` различает три источника. Токены берутся из
    `response.usage`; стоимость не храним — это оценка на read-time по тарифу.

    Без FK на `lots`/`users`: запись должна пережить удаление лота/юзера и не
    падать на dev-анониме (id=0 без строки в `users`). Учёт — это лог, не граф.
    """

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # analyze|match|chat
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    lot_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, index=True)


# --- Автоподача заявок (TENDER_AUTOSUBMIT_PLAN.md) ------------------------------
# Статус-машина Submission. Пред-стейдж невозможен (кнопка «Подать» гейтится до
# time_open), поэтому весь визард идёт в гонке после открытия:
#   PLANNED  — запланировано, ждём open_at
#   ARMED    — агент прогрет (залогинен, на странице объявления, PIN разблокирован),
#              ждёт появления кнопки «Подать заявку»
#   FIRING   — визард выполняется (лоты→документы→шифрование цены→preview→подача)
#   SUBMITTED— ajax_public_application принят сервером
#   CONFIRMED— квитанция подтверждена (заявка в реестре поданных)
#   FAILED   — ошибка на любом шаге
#   SKIPPED  — отменено правилом (нет eligibility / отозвано)
SUBMISSION_STATUSES = (
    "PLANNED",
    "DISPATCHING",  # застолблена диспетчером, задача уходит агенту (claim до отправки)
    "ARMED",
    "FIRING",
    "SUBMITTED",
    "CONFIRMED",
    "FAILED",
    "SKIPPED",
    "UNKNOWN",  # выстрел ушёл, ответ не получен (ReadTimeout) — состояние неясно
)

# Ранг «продвижения» — apply_result не откатывает подачу на более ранний
# не-терминальный статус при поздней/повторной доставке отчёта (P0-apply).
SUBMISSION_STATUS_RANK = {name: i for i, name in enumerate(SUBMISSION_STATUSES)}

# Терминальные статусы: попав сюда, подача больше не меняется — поздний отчёт
# агента (ретрай/редоставка) не перезаписывает исход (в т.ч. CONFIRMED).
SUBMISSION_TERMINAL_STATUSES = frozenset({"CONFIRMED", "FAILED", "SKIPPED", "UNKNOWN"})


class ClientCredential(Base):
    """Секреты клиента для подачи от его имени — всё шифровано (KeyVault, §8).

    На диск/в БД попадает ТОЛЬКО шифртекст AES-256-GCM (vault/crypto.py) + nonce.
    Три секрета: `.p12` signing-ключ (подпись/шифрование цены), пароль учётки
    портала (двухфактор входа goszakup, гайд §2.4) и PIN ключа. Расшифровка —
    в момент использования на Windows submit-agent, не на read-time UI.

    БЕЗ связи с `User`/лотами: это операционная сущность подачи, отдельная от
    multi-user-входа в UI. `iin_bin` — для человекочитаемой привязки и аудита.
    """

    __tablename__ = "client_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    iin_bin: Mapped[str | None] = mapped_column(String(12), index=True)

    p12_enc: Mapped[str] = mapped_column(Text)
    p12_nonce: Mapped[str] = mapped_column(String(32))
    portal_password_enc: Mapped[str] = mapped_column(Text)
    portal_password_nonce: Mapped[str] = mapped_column(String(32))
    key_pin_enc: Mapped[str | None] = mapped_column(Text)
    key_pin_nonce: Mapped[str | None] = mapped_column(String(32))

    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, onupdate=_now)

    submissions: Mapped[list[Submission]] = relationship(back_populates="credential")


class Submission(Base):
    """Запланированная автоподача заявки на конкурс и её исполнение.

    Главное требование — СКОРОСТЬ: «кто первым подал — выигрывает». Пред-стейдж
    черновика невозможен, поэтому весь визард выполняется в гонке после `open_at`
    (см. TENDER_AUTOSUBMIT_PLAN.md §1). Статус-машина — `SUBMISSION_STATUSES`.

    `bid_enc`/`bid_nonce` — ЗАШИФРОВАННЫЙ план цены `{lot_id: price}`. Цена в
    sealed-bid секретна до вскрытия комиссией; держать её plaintext в нашей БД =
    утечкой раскрыть ставку конкурентам. `lot_ids` (несекретно) — какие лоты
    берём, для отображения/планирования.
    """

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    credential_id: Mapped[int] = mapped_column(
        ForeignKey("client_credentials.id"), index=True
    )
    # id объявления на goszakup (trd_buy_id) и опц. человекочитаемый номер.
    anno_id: Mapped[int] = mapped_column(BigInteger, index=True)
    anno_number: Mapped[str | None] = mapped_column(String(40))

    lot_ids: Mapped[list | None] = mapped_column(JSON_TYPE)
    bid_enc: Mapped[str] = mapped_column(Text)
    bid_nonce: Mapped[str] = mapped_column(String(32))

    # Момент, когда «Подать заявку» становится доступной (цель гонки), и дедлайн.
    open_at: Mapped[datetime] = mapped_column(TS_TYPE, index=True)
    close_at: Mapped[datetime | None] = mapped_column(TS_TYPE)

    status: Mapped[str] = mapped_column(
        String(20), default="PLANNED", server_default="PLANNED",
        nullable=False, index=True,
    )
    agent_node: Mapped[str | None] = mapped_column(String(80))
    # id черновика заявки на портале (известен только после старта визарда).
    app_id: Mapped[int | None] = mapped_column(BigInteger)

    armed_at: Mapped[datetime | None] = mapped_column(TS_TYPE)
    fired_at: Mapped[datetime | None] = mapped_column(TS_TYPE)
    submitted_at: Mapped[datetime | None] = mapped_column(TS_TYPE)
    confirmed_at: Mapped[datetime | None] = mapped_column(TS_TYPE)
    # Латентность от open_at до принятия сервером — метрика «успели/нет».
    fire_latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    result: Mapped[dict | None] = mapped_column(JSON_TYPE)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now)
    updated_at: Mapped[datetime] = mapped_column(TS_TYPE, default=_now, onupdate=_now)

    credential: Mapped[ClientCredential] = relationship(back_populates="submissions")
