"""Ingest-time validation of the tariff catalog.

Two severities:
* errors   -- objectively invalid states (wrong enum, an unlimited total with no
              unlimited component, a 90-day package priced "monthly", ...). Any error
              aborts the ingest before the database is touched.
* warnings -- legitimate-but-uncertain business information (activation channel not
              stated in the source). Reported, never blocking.

Rules check internal consistency only; they never invent or repair values.
"""
import math
from dataclasses import dataclass, field
from datetime import date

from app.ingest.normalize import (
    ACTIVATION_CHANNELS,
    BENEFIT_AFTER_TRIAL,
    BENEFIT_CATEGORIES,
    BENEFIT_ELIGIBILITY,
    BENEFIT_OFFERS,
    BENEFITS_STATUSES,
    COMMITMENT_STATUSES,
    LINE_TYPE_LABELS,
    OPERATOR_LABELS,
    PRICE_PERIODS,
    RENEWAL_TYPES,
    BONUS_INTERNET_SCOPES,
    UNLIMITED,
    benefit_tags,
)

# The current catalog holds new-customer offers only (see README "Data"). A record in
# any other bucket would silently widen who is told they are eligible, so it is an
# error until the product deliberately supports it.
ALLOWED_CUSTOMER_TYPES = {"new"}

# Retired lifecycle fields. A record carrying one is a
# sign that an inactive offer was pasted back in.
RETIRED_KEYS = ("availability", "availability_note", "closed_on", "superseded_by")
# Operator wording for "closed to new customers" -- current evidence can never say this.
CLOSURE_PHRASES = ("kapatılmıştır", "kapalıdır", "Kampanya Bitti", "kapanmıştır")

_GB_FIELDS = ("internet_gb", "social_media_gb", "bonus_internet_gb")
_COUNT_FIELDS = ("voice_minutes", "sms_count")


class CatalogValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} invalid record(s):\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _label(rec: dict) -> str:
    return f"{rec.get('operator', '?')}/{rec.get('line_type', '?')}/{rec.get('name', '?')}"


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_quantity(rec, name, errors, *, required=False):
    value = rec.get(name)
    if value is None:
        if required:
            errors.append(f"{_label(rec)}: {name} is required")
        return
    if not _is_num(value) or (value < 0 and value != UNLIMITED):
        errors.append(f"{_label(rec)}: {name}={value!r} must be >= 0 or {UNLIMITED} (unlimited)")


def _check_internet(rec, errors):
    if rec.get("internet_gb") is None:
        errors.append(f"{_label(rec)}: internet_gb (general internet) is required")
    if "total_internet_gb" in rec and "benefits_status" in rec:
        # This obsolete field summed general, social-only, hotspot-only and app-only
        # data into one misleading number. general_internet_gb() derives the honest total.
        errors.append(f"{_label(rec)}: total_internet_gb is obsolete; use the component fields")


def _check_price(rec, errors):
    price = rec.get("price_try")
    if not _is_num(price) or price <= 0:
        errors.append(f"{_label(rec)}: price_try={price!r} must be a positive number")
    period = rec.get("price_period")
    if period not in PRICE_PERIODS:
        errors.append(f"{_label(rec)}: price_period={period!r} not in {sorted(PRICE_PERIODS)}")
        return
    renewal = rec.get("renewal_type")
    if renewal is not None and renewal not in RENEWAL_TYPES:
        errors.append(f"{_label(rec)}: renewal_type={renewal!r} not in {sorted(RENEWAL_TYPES)}")
    if renewal == "one_time" and rec.get("promo_months"):
        errors.append(f"{_label(rec)}: a one-time package cannot have a later price (promo_months)")
    days = rec.get("validity_days")
    if rec.get("line_type") == "postpaid":
        if period != "month":
            errors.append(f"{_label(rec)}: postpaid price_period must be 'month', got {period!r}")
        # validity_days is optional for postpaid (allowances follow the billing month).
        if days is not None and (not isinstance(days, int) or days <= 0):
            errors.append(f"{_label(rec)}: validity_days={days!r} must be a positive integer or null")
        return
    if not isinstance(days, int) or days <= 0:
        errors.append(f"{_label(rec)}: prepaid validity_days={days!r} must be a positive integer")
        return
    # A prepaid price pays for one package: its period IS the allowance validity.
    if period != f"{days}_day":
        errors.append(
            f"{_label(rec)}: price_period={period!r} contradicts validity_days={days} "
            f"(a prepaid price covers one package: expected '{days}_day')"
        )


def _check_commitment(rec, errors):
    status, months = rec.get("commitment_status"), rec.get("commitment_months")
    no_commit_price = rec.get("no_commitment_price_try")
    if status not in COMMITMENT_STATUSES:
        errors.append(f"{_label(rec)}: commitment_status={status!r} invalid")
    if rec.get("line_type") == "prepaid":
        if status != "no_commitment" or months is not None or no_commit_price is not None:
            errors.append(f"{_label(rec)}: prepaid records cannot carry a commitment")
        return
    if status == "committed" and not (isinstance(months, int) and months > 0):
        errors.append(f"{_label(rec)}: committed requires commitment_months > 0")
    if status != "committed" and months is not None:
        errors.append(f"{_label(rec)}: commitment_months set but status is {status!r}")
    if no_commit_price is not None and _is_num(rec.get("price_try")) and no_commit_price <= rec["price_try"]:
        errors.append(f"{_label(rec)}: no_commitment_price_try must exceed the committed price")
    lock = rec.get("price_lock_months")
    if lock is not None and not (isinstance(lock, int) and lock > 0):
        errors.append(f"{_label(rec)}: price_lock_months={lock!r} must be a positive integer")


def _check_derived_text(rec, errors):
    """The rendered document must not overstate general internet."""
    doc = rec.get("doc_text") or ""
    if not doc.strip():
        errors.append(f"{_label(rec)}: doc_text is empty")
    general_unlimited = rec.get("internet_gb") == UNLIMITED
    if ("sinirsiz genel internet" in doc) != general_unlimited:
        errors.append(f"{_label(rec)}: doc_text unlimited-internet wording disagrees with internet_gb")
    tags = set(benefit_tags(rec))
    if ("unlimited_data" in tags) != general_unlimited:
        errors.append(f"{_label(rec)}: unlimited_data tag disagrees with internet_gb")
    # A Netflix tag means a Netflix membership, never data that Netflix may use.
    subs = [str(b.get("service", "")).lower() for b in rec.get("benefits") or [] if b.get("category") == "subscription"]
    if "netflix" in tags and not any("netflix" in s for s in subs):
        errors.append(f"{_label(rec)}: netflix tag without a Netflix subscription benefit")


_BENEFIT_KEYS = {
    "category", "service", "offer", "duration_months", "after_trial", "after_trial_price_try",
    "amount_gb", "apps", "value_try", "eligibility", "activation", "note",
}


def _check_benefits(rec, errors):
    """Structured benefits: the shape that keeps 'free trial', 'app data' and 'discount'
    from being read as 'included subscription'. Absence is never a negative claim:
    verified_none (page read, nothing listed) and unverified (page not readable) differ."""
    status, benefits = rec.get("benefits_status"), rec.get("benefits")
    if status is None and benefits is None:
        return  # pre-2D record shape; the catalog requires both (validate_provenance)
    if status not in BENEFITS_STATUSES:
        errors.append(f"{_label(rec)}: benefits_status={status!r} not in {sorted(BENEFITS_STATUSES)}")
        return
    if not isinstance(benefits, list):
        errors.append(f"{_label(rec)}: benefits must be a list")
        return
    if status == "verified" and not benefits:
        errors.append(f"{_label(rec)}: benefits_status=verified needs at least one benefit (use verified_none)")
    if status in ("verified_none", "unverified") and benefits:
        errors.append(f"{_label(rec)}: benefits_status={status} cannot list benefits")
    for i, b in enumerate(benefits):
        where = f"{_label(rec)}: benefits[{i}]"
        if not isinstance(b, dict):
            errors.append(f"{where} must be an object")
            continue
        unknown = set(b) - _BENEFIT_KEYS
        if unknown:
            errors.append(f"{where} unknown key(s) {sorted(unknown)}")
        category, offer = b.get("category"), b.get("offer")
        if category not in BENEFIT_CATEGORIES:
            errors.append(f"{where} category={category!r} invalid")
        if offer not in BENEFIT_OFFERS:
            errors.append(f"{where} offer={offer!r} invalid")
        if not str(b.get("service") or "").strip():
            errors.append(f"{where} service is required")
        months = b.get("duration_months")
        if months is not None and not (isinstance(months, int) and months > 0):
            errors.append(f"{where} duration_months={months!r} must be a positive integer")
        if offer == "free_trial":
            if months is None:
                errors.append(f"{where} a free_trial needs duration_months")
            if b.get("after_trial") not in BENEFIT_AFTER_TRIAL:
                errors.append(f"{where} a free_trial needs after_trial in {sorted(BENEFIT_AFTER_TRIAL)}")
        elif "after_trial" in b or "after_trial_price_try" in b:
            errors.append(f"{where} after_trial only applies to a free_trial")
        if b.get("after_trial_price_try") is not None and b.get("after_trial") != "paid":
            errors.append(f"{where} after_trial_price_try requires after_trial=paid")
        if offer == "requires_purchase" and category != "app_data":
            errors.append(f"{where} requires_purchase only describes app_data")
        if category == "app_data":
            if not b.get("apps"):
                errors.append(f"{where} app_data must name its apps")
            amount = b.get("amount_gb")
            if not _is_num(amount) or (amount <= 0 and amount != UNLIMITED):
                errors.append(f"{where} app_data amount_gb={amount!r} must be > 0 or {UNLIMITED}")
        elif "apps" in b:
            errors.append(f"{where} apps only apply to app_data")
        if category == "bonus_data" and b.get("amount_gb") is not None and not (_is_num(b["amount_gb"]) and b["amount_gb"] > 0):
            errors.append(f"{where} bonus_data amount_gb must be > 0 when known")
        if category == "voucher" and not (_is_num(b.get("value_try")) and b["value_try"] > 0):
            errors.append(f"{where} a voucher needs its value_try")
        if b.get("eligibility") is not None and b["eligibility"] not in BENEFIT_ELIGIBILITY:
            errors.append(f"{where} eligibility={b['eligibility']!r} invalid")


def _check_channels_and_promo(rec, errors):
    channels = rec.get("activation_channels")
    if channels is not None and (
        not isinstance(channels, list) or not channels or set(channels) - set(ACTIVATION_CHANNELS)
    ):
        errors.append(f"{_label(rec)}: activation_channels={channels!r} must be null (unknown) or a non-empty subset of {list(ACTIVATION_CHANNELS)}")
    apps = rec.get("social_media_apps")
    if apps is not None and (not isinstance(apps, list) or not apps):
        errors.append(f"{_label(rec)}: social_media_apps must be null or a non-empty list")
    if apps and rec.get("social_media_gb") is None:
        errors.append(f"{_label(rec)}: social_media_apps given without a social_media_gb quota")
    scope = rec.get("bonus_internet_scope")
    if scope is not None and scope not in BONUS_INTERNET_SCOPES:
        errors.append(f"{_label(rec)}: bonus_internet_scope={scope!r} not in {sorted(BONUS_INTERNET_SCOPES)}")
    if scope is not None and rec.get("bonus_internet_gb") is None:
        errors.append(f"{_label(rec)}: bonus_internet_scope given without bonus_internet_gb")
    if "benefits_status" in rec and rec.get("bonus_internet_gb") is not None and scope is None:
        # Catalog records: a bonus must say where it works (general vs hotspot vs app-only).
        errors.append(f"{_label(rec)}: bonus_internet_gb needs bonus_internet_scope")
    months, after = rec.get("promo_months"), rec.get("price_after_promo_try")
    if (months is None) != (after is None):
        errors.append(f"{_label(rec)}: promo_months and price_after_promo_try go together")
    elif months is not None:
        if not (isinstance(months, int) and months > 0):
            errors.append(f"{_label(rec)}: promo_months={months!r} must be a positive integer")
        if not _is_num(after) or (_is_num(rec.get("price_try")) and after <= rec["price_try"]):
            errors.append(f"{_label(rec)}: price_after_promo_try must exceed the promotional price_try")


def validate_record(rec: dict, report: ValidationReport) -> None:
    errors = report.errors
    if rec.get("operator") not in OPERATOR_LABELS:
        errors.append(f"{_label(rec)}: unknown operator {rec.get('operator')!r}")
    if rec.get("line_type") not in LINE_TYPE_LABELS:
        errors.append(f"{_label(rec)}: line_type={rec.get('line_type')!r} invalid")
    if rec.get("customer_type") not in ALLOWED_CUSTOMER_TYPES:
        errors.append(
            f"{_label(rec)}: customer_type={rec.get('customer_type')!r}; the catalog is "
            f"new-customer only ({sorted(ALLOWED_CUSTOMER_TYPES)})"
        )
    if not str(rec.get("name") or "").strip():
        errors.append(f"{_label(rec)}: name is required")
    if not str(rec.get("source_url") or "").startswith("https://"):
        errors.append(f"{_label(rec)}: source_url must be an https URL")
    for name in _GB_FIELDS:
        _check_quantity(rec, name, errors)
    _check_quantity(rec, "voice_minutes", errors, required=True)
    _check_quantity(rec, "sms_count", errors)
    _check_internet(rec, errors)
    _check_price(rec, errors)
    _check_commitment(rec, errors)
    age_min, age_max = rec.get("age_min"), rec.get("age_max")
    if age_min is not None and age_max is not None and age_min > age_max:
        errors.append(f"{_label(rec)}: age_min > age_max")
    _check_benefits(rec, errors)
    _check_channels_and_promo(rec, errors)
    _check_derived_text(rec, errors)

    redundant = set(rec.get("eligibility_tags") or []) & {*ACTIVATION_CHANNELS, "age_restricted"}
    if redundant:
        # One authoritative field per fact: activation_channels and age_min/age_max.
        errors.append(f"{_label(rec)}: eligibility_tags repeat {sorted(redundant)}; use activation_channels / age_min / age_max")
    if not rec.get("activation_channels"):
        report.warnings.append(f"{_label(rec)}: activation channel (yeni hat / numara tasima) not stated in source")


def _iso_date(value) -> bool:
    if value is None:
        return False
    try:
        date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def validate_provenance(rec: dict, report: ValidationReport) -> None:
    """Every record is a verified current offer with traceable evidence."""
    errors = report.errors
    retired = [k for k in RETIRED_KEYS if k in rec]
    if retired:
        errors.append(
            f"{_label(rec)}: lifecycle field(s) {retired} -- the data files hold current offers only; "
            "remove closed offers from the active data files"
        )
    verified = rec.get("verified_at")
    if not _iso_date(verified):
        errors.append(f"{_label(rec)}: verified_at={verified!r} must be the ISO date the source was checked")
    evidence = str(rec.get("evidence") or "")
    if not evidence.strip():
        errors.append(f"{_label(rec)}: evidence (official-source quote) is required")
    elif any(phrase in evidence for phrase in CLOSURE_PHRASES):
        errors.append(f"{_label(rec)}: evidence quotes a closure notice; a closed offer cannot be in the catalog")
    # Every catalog record states what its page says about benefits
    # and channels, including "nothing" and "unknown" -- an absent key is not a claim.
    for key in ("benefits", "benefits_status", "activation_channels", "renewal_type"):
        if key not in rec:
            errors.append(f"{_label(rec)}: {key} is required (use [] / 'unverified' / null for unknown)")
def find_duplicate_identities(records: list[dict], keys=("operator", "line_type", "customer_type", "name")) -> list[str]:
    seen: dict[tuple, int] = {}
    for rec in records:
        key = tuple(rec.get(k) for k in keys)
        seen[key] = seen.get(key, 0) + 1
    return [f"duplicate identity {key} x{count}" for key, count in seen.items() if count > 1]


def validate_catalog(records: list[dict]) -> ValidationReport:
    """Validate normalized records. Raises CatalogValidationError on any error."""
    report = ValidationReport()
    report.errors.extend(find_duplicate_identities(records, keys=("operator", "line_type", "name")))
    for rec in records:
        validate_record(rec, report)
    if report.errors:
        raise CatalogValidationError(report.errors)
    return report


def validate_embeddings(vectors, count: int, dim: int) -> list[list[float]]:
    """One finite, non-zero vector of the expected dimension per record."""
    vectors = [list(map(float, v)) for v in vectors]
    if len(vectors) != count:
        raise ValueError(f"expected {count} embeddings, got {len(vectors)}")
    for i, vec in enumerate(vectors):
        if len(vec) != dim:
            raise ValueError(f"embedding {i} has {len(vec)} dims, expected {dim}")
        if not all(math.isfinite(x) for x in vec):
            raise ValueError(f"embedding {i} contains NaN/inf")
        if not any(vec):
            raise ValueError(f"embedding {i} is all zeros")
    return vectors
