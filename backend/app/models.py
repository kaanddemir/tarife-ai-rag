import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field
from sqlalchemy import Numeric, String, Text, Integer, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings


class Base(DeclarativeBase):
    pass


class Package(Base):
    """A single mobile package/tariff from an operator's official site."""

    __tablename__ = "packages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    operator: Mapped[str] = mapped_column(String(32), index=True)  # turkcell | vodafone | turk_telekom
    name: Mapped[str] = mapped_column(String(256))
    line_type: Mapped[str] = mapped_column(String(16), index=True, default="prepaid")  # prepaid | postpaid
    package_group: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # standard | gnc | welcome | ...

    price_try: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    price_period: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # month | 28_day | 3_month | ...
    price_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    internet_gb: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)       # genel/base internet
    social_media_gb: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)   # sosyal medya kotasi
    bonus_internet_gb: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True) # paylasim/bonus
    total_internet_gb: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True) # baslik/karsilastirma
    voice_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # -1 = unlimited
    sms_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)      # -1 = unlimited
    validity_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    validity_unit: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # day | week | month
    validity_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    conditions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    faq: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # [{q, a}, ...]

    source_url: Mapped[str] = mapped_column(Text)
    listing_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    doc_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(settings.embed_dim), nullable=True)


class MessageUsage(Base):
    """One row per successful /chat message, used to enforce per-user and per-IP
    message quotas (see app/quota.py). Written by the backend as the postgres role."""

    __tablename__ = "message_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    is_anonymous: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc)
    )


# ---- Pydantic schemas (API I/O) ----

class FaqItem(BaseModel):
    q: str
    a: str


class PackageOut(BaseModel):
    id: uuid.UUID
    operator: str
    name: str
    line_type: str
    package_group: Optional[str] = None
    price_try: Optional[float] = None
    price_period: Optional[str] = None
    price_note: Optional[str] = None
    internet_gb: Optional[float] = None
    social_media_gb: Optional[float] = None
    bonus_internet_gb: Optional[float] = None
    total_internet_gb: Optional[float] = None
    voice_minutes: Optional[int] = None
    sms_count: Optional[int] = None
    validity_value: Optional[int] = None
    validity_unit: Optional[str] = None
    validity_days: Optional[int] = None
    conditions: Optional[str] = None
    campaign: Optional[str] = None
    faq: Optional[list[FaqItem]] = None
    source_url: str
    listing_url: Optional[str] = None

    model_config = {"from_attributes": True}


class Citation(BaseModel):
    id: str          # stable source id used in the answer, e.g. "S1"
    operator: str
    name: str
    source_url: str


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    # Caps a stored turn; must stay above a full assistant answer (max_tokens ~1100)
    # so replaying history never trips the limit.
    content: str = Field(max_length=3500)


class ChatRequest(BaseModel):
    # Focused tariff Q&A, not a generic chatbot: a real question is well under 400
    # chars, and only the last 5 turns are ever used (see rag/history.HISTORY_LIMIT).
    question: str = Field(min_length=1, max_length=400)
    history: list[ChatTurn] = Field(default_factory=list, max_length=8)


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    packages: list[PackageOut]
