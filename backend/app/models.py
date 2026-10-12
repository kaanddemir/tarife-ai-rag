import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field
from sqlalchemy import CheckConstraint, Numeric, String, Text, Integer, DateTime, UniqueConstraint, case, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings
from app.ingest.normalize import benefit_tags, general_internet_gb


class Base(DeclarativeBase):
    pass


# Python None must be stored as SQL NULL: the default JSONB type writes the JSON literal
# 'null', which "IS NULL" does not match (5 unknown activation channels were invisible).
_JSONList = JSONB(none_as_null=True)


class Package(Base):
    """A single mobile package/tariff from an operator's official site."""

    __tablename__ = "packages"
    # Database-level guards for the same invariants the ingest validator checks, so a
    # hand-written INSERT cannot store an unlimited value of the wrong sign, a prepaid
    # commitment or an unknown enum. Keep these in step with the current baseline.
    __table_args__ = (
        UniqueConstraint("operator", "line_type", "name", name="uq_packages_identity"),
        CheckConstraint("operator IN ('turkcell', 'vodafone', 'turk_telekom')", name="ck_packages_operator"),
        CheckConstraint("line_type IN ('prepaid', 'postpaid')", name="ck_packages_line_type"),
        CheckConstraint("customer_type IN ('new', 'existing', 'any')", name="ck_packages_customer_type"),
        CheckConstraint("commitment_status IN ('committed', 'no_commitment', 'unknown')", name="ck_packages_commitment_status"),
        CheckConstraint(
            "price_period IS NULL OR price_period IN ('month', '28_day', '60_day', '90_day', '180_day', '360_day')",
            name="ck_packages_price_period",
        ),
        CheckConstraint("price_try IS NULL OR price_try > 0", name="ck_packages_price_positive"),
        CheckConstraint(
            "(internet_gb IS NULL OR internet_gb >= 0 OR internet_gb = -1) AND "
            "(social_media_gb IS NULL OR social_media_gb >= 0 OR social_media_gb = -1) AND "
            "(bonus_internet_gb IS NULL OR bonus_internet_gb >= 0 OR bonus_internet_gb = -1) AND "
            "(voice_minutes IS NULL OR voice_minutes >= 0 OR voice_minutes = -1) AND "
            "(sms_count IS NULL OR sms_count >= 0 OR sms_count = -1)",
            name="ck_packages_quantities",
        ),
        CheckConstraint("age_min IS NULL OR age_max IS NULL OR age_min <= age_max", name="ck_packages_age_range"),
        CheckConstraint(
            "line_type <> 'prepaid' OR (commitment_months IS NULL AND no_commitment_price_try IS NULL)",
            name="ck_packages_prepaid_no_commitment",
        ),
        CheckConstraint("benefits_status IN ('verified', 'verified_none', 'unverified')", name="ck_packages_benefits_status"),
        CheckConstraint(
            "(promo_months IS NULL) = (price_after_promo_try IS NULL) AND (promo_months IS NULL OR promo_months > 0)",
            name="ck_packages_promo_pair",
        ),
        # Canonical JSON-array fields.
        CheckConstraint(
            " AND ".join(f"({c} IS NULL OR jsonb_typeof({c}) = 'array')" for c in (
                "activation_channels", "social_media_apps", "eligibility_tags", "benefits")),
            name="ck_packages_json_arrays",
        ),
        CheckConstraint(
            "(bonus_internet_gb IS NULL) = (bonus_internet_scope IS NULL) AND (bonus_internet_scope IS NULL OR "
            "bonus_internet_scope IN ('general', 'tethering_only', 'app_specific'))",
            name="ck_packages_bonus_scope",
        ),
        # Canonical renewal and billing-period fields.
        CheckConstraint("renewal_type IN ('recurring', 'one_time', 'unknown')", name="ck_packages_renewal_type"),
        CheckConstraint(
            "CASE WHEN line_type = 'prepaid' THEN price_period = validity_days || '_day' ELSE price_period = 'month' END",
            name="ck_packages_period_by_line",
        ),
        CheckConstraint(
            "eligibility_tags IS NULL OR NOT "
            "(eligibility_tags ?| ARRAY['new_line','number_porting','age_restricted'])",
            name="ck_packages_eligibility_tags",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    operator: Mapped[str] = mapped_column(String(32), index=True)  # turkcell | vodafone | turk_telekom
    name: Mapped[str] = mapped_column(String(256))
    line_type: Mapped[str] = mapped_column(String(16), index=True, default="prepaid")  # prepaid | postpaid
    # "any" = package open to both new and existing customers (shows on both listing pages).
    customer_type: Mapped[str] = mapped_column(String(16), index=True, default="any")  # new | existing | any
    # The operator's marketing family is kept separate from the cross-operator
    # comparison axes (the old package_group column was always equal to it; see below).
    operator_family: Mapped[Optional[str]] = mapped_column(String(48), nullable=True, index=True)
    audience_segment: Mapped[Optional[str]] = mapped_column(String(24), nullable=True, index=True)
    product_scope: Mapped[str] = mapped_column(String(24), index=True, default="mobile_voice")
    age_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    age_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    eligibility_tags: Mapped[Optional[list]] = mapped_column(_JSONList, nullable=True)
    # new_line / number_porting; NULL = the source does not say (never "both").
    activation_channels: Mapped[Optional[list]] = mapped_column(_JSONList, nullable=True)
    # Typed benefits (see ingest/normalize.py BENEFIT_*); benefits_status separates
    # "page read, nothing listed" (verified_none) from "not readable" (unverified).
    benefits: Mapped[Optional[list]] = mapped_column(_JSONList, nullable=True)
    benefits_status: Mapped[str] = mapped_column(String(16), default="unverified", server_default="unverified")

    price_try: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    # What price_try pays for: one billing "month" (postpaid) or one prepaid package of
    # validity_days ("<N>_day"). Renewal is a separate fact (renewal_type).
    price_period: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # recurring | one_time | unknown (the source does not say) -- never inferred.
    renewal_type: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown")
    # Distinct things: commitment binds you (early-termination fee), a price lock only freezes the price.
    commitment_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # duration when status=committed
    commitment_status: Mapped[str] = mapped_column(String(20), default="unknown")
    price_lock_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # None = no fixed-price period
    # price_try is the committed price (postpaid); this is the higher price without a
    # commitment, when the source lists one. Always None for prepaid.
    no_commitment_price_try: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    # An introductory price: price_try applies for promo_months, then price_after_promo_try.
    promo_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_after_promo_try: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    internet_gb: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)       # general/base internet
    social_media_gb: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)   # sosyal medya kotasi
    social_media_apps: Mapped[Optional[list]] = mapped_column(_JSONList, nullable=True)  # apps the quota covers
    bonus_internet_gb: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True) # extra/gift data
    # Where the bonus works: general | tethering_only (internet paylasimi) | app_specific.
    bonus_internet_scope: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    voice_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # -1 = unlimited
    sms_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)      # -1 = unlimited
    # How long a prepaid package's allowances last. NULL for postpaid (allowances follow the bill).
    validity_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    conditions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    campaign: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source_url: Mapped[str] = mapped_column(Text)
    # When the record's facts were last verified on the official source (UTC midnight)
    # -- never the ingest time. The legacy column name was scraped_at.
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    doc_text: Mapped[str] = mapped_column(Text)
    # Deferred: only SQL expressions (distance, IS NULL) use it. Loading 1024 floats per
    # row into every Package made the retrieval query ~8x slower in local profiling.
    embedding: Mapped[Optional[list]] = mapped_column(Vector(settings.embed_dim), nullable=True, deferred=True)
    # Reproducibility: which model embedded which exact document text.
    embedding_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    doc_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # sha256(doc_text)

    @property
    def benefit_tags(self) -> list[str]:
        """Derived from `benefits` and the structured fields on every read (never stored)."""
        return benefit_tags({c.name: getattr(self, c.name) for c in self.__table__.columns if c.name != "embedding"})

    @hybrid_property
    def general_internet_gb(self) -> Optional[float]:
        """General-use internet: internet_gb plus a bonus only when its scope is general."""
        return general_internet_gb(
            None if self.internet_gb is None else float(self.internet_gb),
            None if self.bonus_internet_gb is None else float(self.bonus_internet_gb),
            self.bonus_internet_scope,
        )

    @general_internet_gb.inplace.expression
    @classmethod
    def _general_internet_gb_sql(cls):
        general_bonus = cls.bonus_internet_scope == "general"
        return case(
            (cls.internet_gb == -1, -1),
            (general_bonus & (cls.bonus_internet_gb == -1), -1),
            (general_bonus, cls.internet_gb + func.coalesce(cls.bonus_internet_gb, 0)),
            else_=cls.internet_gb,
        )

    @property
    def price_30d_equivalent(self) -> Optional[float]:
        """Comparison value only (rounded TL); the stored price is never changed."""
        if self.line_type != "prepaid" or not self.validity_days or self.price_try is None or self.validity_days == 30:
            return None
        return float(round(float(self.price_try) * 30 / self.validity_days))

    @property
    def package_group(self) -> Optional[str]:
        """API compatibility: the dropped package_group column always equalled operator_family."""
        return self.operator_family


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


class QuotaReservation(Base):
    """A message slot held while a /chat request is in flight (see app/quota.py).

    Counted against the quota together with message_usage, so parallel requests
    cannot all pass the check. Converted to a message_usage row on success, deleted
    on failure, and ignored once expires_at passes (a crashed request). A separate
    table so reservations can expire independently without changing usage history."""

    __tablename__ = "quota_reservation"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    ip: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    is_anonymous: Mapped[bool] = mapped_column(default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


# ---- Pydantic schemas (API I/O) ----

class PackageOut(BaseModel):
    id: uuid.UUID
    operator: str
    name: str
    line_type: str
    customer_type: str
    package_group: Optional[str] = None
    operator_family: Optional[str] = None
    audience_segment: Optional[str] = None
    product_scope: str = "mobile_voice"
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    eligibility_tags: Optional[list[str]] = None
    benefit_tags: Optional[list[str]] = None
    activation_channels: Optional[list[str]] = None
    benefits: Optional[list[dict]] = None
    benefits_status: Optional[str] = None
    price_try: Optional[float] = None
    price_period: Optional[str] = None
    renewal_type: Optional[str] = None
    commitment_months: Optional[int] = None
    commitment_status: str = "unknown"
    price_lock_months: Optional[int] = None
    no_commitment_price_try: Optional[float] = None
    promo_months: Optional[int] = None
    price_after_promo_try: Optional[float] = None
    internet_gb: Optional[float] = None
    social_media_gb: Optional[float] = None
    social_media_apps: Optional[list[str]] = None
    bonus_internet_gb: Optional[float] = None
    bonus_internet_scope: Optional[str] = None
    # Derived general-use internet; restricted quotas are never summed in.
    general_internet_gb: Optional[float] = None
    # TarifeAI's 30-day comparison value of a prepaid package (price * 30 / validity_days);
    # None for postpaid or 30-day packages. Never what the operator bills.
    price_30d_equivalent: Optional[float] = None
    voice_minutes: Optional[int] = None
    sms_count: Optional[int] = None
    validity_days: Optional[int] = None
    conditions: Optional[str] = None
    campaign: Optional[str] = None
    source_url: str
    # When the facts were last checked on the official page (not the ingest time).
    verified_at: Optional[datetime] = None
    # Retrieval verdict, set only on /chat results: "match" (meets every stated
    # requirement), "unverified" (a requirement could not be checked from the source) or
    # "compromise" (fails one; shown as an alternative). None outside retrieval.
    retrieval_status: Optional[str] = None
    # Per-requirement result for the user's question: (label, True/False/None=unknown).
    retrieval_checks: Optional[list[tuple[str, Optional[bool]]]] = None

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
