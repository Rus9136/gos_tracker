"""SQLAlchemy 2.0 модели."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.utcnow()


class Organization(Base):
    """Заказчик / организатор / поставщик. Одно лицо может быть во всех ролях."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bin: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(500), index=True)
    address: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(String(100))
    email: Mapped[Optional[str]] = mapped_column(String(200))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    organized_announcements: Mapped[list["Announcement"]] = relationship(
        back_populates="organizer", foreign_keys="Announcement.organizer_id"
    )
    customer_lots: Mapped[list["Lot"]] = relationship(
        back_populates="customer", foreign_keys="Lot.customer_id"
    )


class Announcement(Base):
    """Объявление о закупке (один или несколько лотов внутри)."""

    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # anno_id с сайта
    number: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    url: Mapped[str] = mapped_column(String(300))
    method: Mapped[Optional[str]] = mapped_column(String(200))
    purchase_type: Mapped[Optional[str]] = mapped_column(String(100))
    subject_type: Mapped[Optional[str]] = mapped_column(String(50))
    organizer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    organizer_address: Mapped[Optional[str]] = mapped_column(Text)
    lots_count: Mapped[Optional[int]] = mapped_column(Integer)
    total_amount: Mapped[Optional[float]] = mapped_column(Float)
    attributes: Mapped[Optional[str]] = mapped_column(Text)  # «Признаки»
    publish_date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    contact_name: Mapped[Optional[str]] = mapped_column(String(300))
    contact_role: Mapped[Optional[str]] = mapped_column(String(200))
    contact_email: Mapped[Optional[str]] = mapped_column(String(200))
    raw: Mapped[Optional[dict]] = mapped_column(JSON)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_synced: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    organizer: Mapped[Optional[Organization]] = relationship(
        back_populates="organized_announcements", foreign_keys=[organizer_id]
    )
    lots: Mapped[list["Lot"]] = relationship(back_populates="announcement")
    documents: Mapped[list["Document"]] = relationship(back_populates="announcement")


class Lot(Base):
    """Лот внутри объявления."""

    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # lot_id с сайта
    number: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    announcement_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("announcements.id"), index=True
    )
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    enstru: Mapped[Optional[str]] = mapped_column(String(500), index=True)
    name: Mapped[Optional[str]] = mapped_column(Text)
    extra: Mapped[Optional[str]] = mapped_column(Text)  # доп. характеристика
    price_per_unit: Mapped[Optional[float]] = mapped_column(Float)
    quantity: Mapped[Optional[float]] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(100))
    plan_amount: Mapped[Optional[float]] = mapped_column(Float, index=True)
    amount_y1: Mapped[Optional[float]] = mapped_column(Float)
    amount_y2: Mapped[Optional[float]] = mapped_column(Float)
    amount_y3: Mapped[Optional[float]] = mapped_column(Float)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    status_name: Mapped[Optional[str]] = mapped_column(String(200), index=True)
    is_actual: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Ручная пометка «интересный лот» из UI (звёздочка на карточке/в списке).
    # Не связана с автоматикой пайплайна — выставляется только пользователем.
    is_starred: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False, index=True
    )
    it_category: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    kato: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    method: Mapped[Optional[str]] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(300))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    last_synced: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    announcement: Mapped[Optional[Announcement]] = relationship(back_populates="lots")
    customer: Mapped[Optional[Organization]] = relationship(
        back_populates="customer_lots", foreign_keys=[customer_id]
    )
    status_history: Mapped[list["LotStatusHistory"]] = relationship(
        back_populates="lot", cascade="all, delete-orphan"
    )
    contracts: Mapped[list["Contract"]] = relationship(back_populates="lot")
    analysis: Mapped[Optional["LotAnalysis"]] = relationship(
        back_populates="lot",
        uselist=False,
        cascade="all, delete-orphan",
    )


class LotStatusHistory(Base):
    __tablename__ = "lot_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    status_name: Mapped[Optional[str]] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)

    lot: Mapped[Lot] = relationship(back_populates="status_history")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("announcement_id", "url", name="uq_doc_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    announcement_id: Mapped[int] = mapped_column(
        ForeignKey("announcements.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(500))
    attribute: Mapped[Optional[str]] = mapped_column(String(200))  # «Признак»
    url: Mapped[str] = mapped_column(String(800))
    local_path: Mapped[Optional[str]] = mapped_column(String(800))
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    size: Mapped[Optional[int]] = mapped_column(Integer)
    content_type: Mapped[Optional[str]] = mapped_column(String(200))
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    download_error: Mapped[Optional[str]] = mapped_column(Text)

    announcement: Mapped[Announcement] = relationship(back_populates="documents")


class Contract(Base):
    """Договор, привязанный к лоту (появляется на финальных стадиях)."""

    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    contract_number: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    status: Mapped[Optional[str]] = mapped_column(String(100))
    plan_amount: Mapped[Optional[float]] = mapped_column(Float)
    contract_amount: Mapped[Optional[float]] = mapped_column(Float)
    fact_amount: Mapped[Optional[float]] = mapped_column(Float)
    supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    supplier_status: Mapped[Optional[str]] = mapped_column(String(100))
    last_synced: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    lot: Mapped[Lot] = relationship(back_populates="contracts")
    supplier: Mapped[Optional[Organization]] = relationship(foreign_keys=[supplier_id])


class Preset(Base):
    """Именованный набор фильтров для ежедневного запуска."""

    __tablename__ = "presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kato: Mapped[Optional[str]] = mapped_column(String(20))
    amount_from: Mapped[Optional[int]] = mapped_column(Integer)
    amount_to: Mapped[Optional[int]] = mapped_column(Integer)
    status_codes: Mapped[Optional[list[int]]] = mapped_column(JSON)
    it_categories: Mapped[Optional[list[str]]] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("presets.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    details_fetched: Mapped[int] = mapped_column(Integer, default=0)
    new_lots: Mapped[int] = mapped_column(Integer, default=0)
    updated_lots: Mapped[int] = mapped_column(Integer, default=0)
    new_documents: Mapped[int] = mapped_column(Integer, default=0)
    llm_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    log_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    # Произвольная подпись для ad-hoc прогонов (preset_id=NULL): отображается
    # на /runs вместо имени preset'а. Например, «БИН 990440004229 · 2020–2026
    # · услуги · статус 360».
    note: Mapped[Optional[str]] = mapped_column(Text)


class LotAnalysis(Base):
    """LLM-классификация лота: dev-категория, стек, краткое ТЗ, риски.

    1:1 к Lot. Идемпотентность через (analyzer_version, tz_sha256): если оба
    совпадают с текущими — переанализ пропускается.
    """

    __tablename__ = "lot_analyses"
    __table_args__ = (UniqueConstraint("lot_id", name="uq_analysis_lot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id"), index=True)
    dev_category: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    tech_stack: Mapped[Optional[list[str]]] = mapped_column(JSON)
    tz_summary: Mapped[Optional[str]] = mapped_column(Text)
    solo_feasible: Mapped[Optional[bool]] = mapped_column(Boolean)
    vendor_lock_risk: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    analysis_confidence: Mapped[Optional[str]] = mapped_column(String(10), index=True)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    analyzer_version: Mapped[str] = mapped_column(String(40))
    tz_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    source_document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id"), index=True
    )
    error: Mapped[Optional[str]] = mapped_column(Text)

    lot: Mapped[Lot] = relationship(back_populates="analysis")
    source_document: Mapped[Optional[Document]] = relationship(
        foreign_keys=[source_document_id]
    )
