"""Hybrid retrieval: parse hard constraints from the question into SQL filters,
then rank the filtered set by pgvector cosine similarity to the query embedding.
Returns full typed Package rows so the API can render exact comparison tables
and ground citations on real values.
"""
import re
from typing import Optional

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest.normalize import UNLIMITED
from app.models import Package
from app.rag.embed import embed_query

# Operator aliases (Turkish, case/spacing tolerant) -> canonical key.
_OPERATOR_ALIASES = {
    "turkcell": "turkcell",
    "vodafone": "vodafone",
    "turk telekom": "turk_telekom",
    "türk telekom": "turk_telekom",
    "turktelekom": "turk_telekom",
    "tt": "turk_telekom",
}


# prepaid pattern must run FIRST: "faturasiz" contains "fatura".
_PREPAID_RE = re.compile(r"faturas[ıi]z|haz[ıi]r ?kart|[oö]n ?[oö]demeli|prepaid", re.I)
_POSTPAID_RE = re.compile(r"fatural[ıi]|postpaid|faturam", re.I)

_NEW_CUSTOMER_RE = re.compile(
    r"yeni m[uü][sş]teri|yeni hat|numara ta[sş]|ta[sş][ıi]y|ge[cç]mek ist|"
    r"ge[cç]i[sş] yap|yeni abone|hen[uü]z .{0,20}yok",
    re.I,
)
# "kullaniyorum" alone is NOT a customer signal: "cok internet kullaniyorum" is a
# usage phrase. It only counts when bound to an operator/line object.
_EXISTING_CUSTOMER_RE = re.compile(
    r"mevcut m[uü][sş]teri|zaten .{0,25}kullan|hatt[ıi]m var|"
    r"aboneyim|m[uü][sş]terisiyim|hatt[ıi]m[ıi] y[uü]kselt|"
    r"(?:turkcell|vodafone|t[uü]rk ?telekom|hatt[ıi]m[ıi])(?:'[a-zçğıöşü]+)?\s+kullan[ıi]yorum",
    re.I,
)

_CHILD_RE = re.compile(
    r"[cç]ocu[gğ]uma|[cç]ocuk i[cç]in|ilk (?:telefon )?hatt[ıi]|ilk hatt[ıi]m|"
    r"ilk turkcellim|ya[sş]a [oö]zel",
    re.I,
)
_YOUTH_RE = re.compile(r"[oö][gğ]renci|gen[cç]|gn[cç]|freezone|selfy", re.I)
_FAMILY_RE = re.compile(r"aile tarifesi|ailece|aile hatt[ıi]|ortak kullan[ıi]m", re.I)
_PUBLIC_RE = re.compile(r"kamu|parlamenter|i[cç]i[sş]leri|devlet memuru", re.I)
_AGE_RE = re.compile(r"\b(\d{1,2})\s*ya[sş](?:[ıi]nda(?:ki)?|inda(?:ki)?| i[cç]in)?\b", re.I)


def detect_operator(q: str) -> Optional[str]:
    low = q.lower()
    for alias, canonical in _OPERATOR_ALIASES.items():
        if alias in low:
            return canonical
    return None


def detect_line_type(q: str) -> Optional[str]:
    if _PREPAID_RE.search(q):
        return "prepaid"
    if _POSTPAID_RE.search(q):
        return "postpaid"
    return None


def detect_customer_type(q: str) -> Optional[str]:
    new, existing = _NEW_CUSTOMER_RE.search(q), _EXISTING_CUSTOMER_RE.search(q)
    if new and not existing:
        return "new"
    if existing and not new:
        return "existing"
    return None  # both or neither -> don't filter, let the embedding do the ranking


def detect_age(q: str) -> Optional[int]:
    match = _AGE_RE.search(q)
    return int(match.group(1)) if match else None


def detect_audience_segment(q: str) -> Optional[str]:
    if _CHILD_RE.search(q):
        return "child"
    if _YOUTH_RE.search(q):
        return "youth"
    if _FAMILY_RE.search(q):
        return "family"
    if _PUBLIC_RE.search(q):
        return "public_special"
    age = detect_age(q)
    if age is not None and age <= 16:
        return "child"
    return None


def detect_operator_family(q: str) -> Optional[str]:
    low = q.lower()
    if re.search(r"i[cç]i[sş]leri", low):
        return "new_internal_affairs"
    if re.search(r"parlamenter", low):
        return "new_parliamentarian"
    return None


def detect_max_price(q: str) -> Optional[float]:
    # "200 TL altinda", "under 200", "en fazla 150 tl", "150 liraya kadar"
    low = q.lower()
    # Without a word boundary, 'az' matches inside "lazim" and silently attaches a
    # price <= 20 filter to the query "20 GB paket lazim".
    if re.search(r"(alt[iı]|under|below|\baz\b|kadar|en fazla|maksimum|max)", low):
        # The first number isn't always the price: "20 GB altinda 150 TL paket" -> 150.
        # Skip numbers immediately followed by GB.
        for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(?:tl|₺|lira)?", low):
            if not re.match(r"\s*gb", low[m.end(1):]):
                return float(m.group(1).replace(",", "."))
    return None


def detect_gb(q: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*gb", q.lower())
    return float(m.group(1).replace(",", ".")) if m else None


def gb_band_filter(gb: float, tolerance: float = 5):
    """GB is a soft target: keep packages within +/- 5 GB of the ask so "compare
    20 GB packages" returns the relevant band, not exact matches only. Unlimited
    (-1) packages satisfy any GB request; they must not be dropped by the band."""
    return or_(
        Package.internet_gb == UNLIMITED,
        (Package.internet_gb >= gb - tolerance) & (Package.internet_gb <= gb + tolerance),
    )


def _eligibility_filters(question: str) -> list:
    """Filters for the "who CAN buy which package" question. Unlike price/GB
    preferences, these are not dropped in the fallback: a postpaid package must not
    be offered to someone asking for prepaid, nor an existing-only package to a new
    customer."""
    # Non-phone device/data products can never enter RAG for any query.
    filters = [Package.product_scope == "mobile_voice"]
    line_type = detect_line_type(question)
    if line_type:
        filters.append(Package.line_type == line_type)

    customer_type = detect_customer_type(question)
    if customer_type:
        # "any" packages are open to both customer types.
        filters.append(Package.customer_type.in_([customer_type, "any"]))
    audience = detect_audience_segment(question)
    if audience:
        filters.append(Package.audience_segment == audience)
    family = detect_operator_family(question)
    if family:
        filters.append(Package.operator_family == family)
    age = detect_age(question)
    if age is not None:
        filters.extend(
            [
                or_(Package.age_min.is_(None), Package.age_min <= age),
                or_(Package.age_max.is_(None), Package.age_max >= age),
            ]
        )
    return filters


def retrieve(session: Session, question: str, k: Optional[int] = None) -> list[Package]:
    k = k or settings.top_k
    eligibility = _eligibility_filters(question)
    stmt = select(Package).where(*eligibility)

    operator = detect_operator(question)
    if operator:
        stmt = stmt.where(Package.operator == operator)

    max_price = detect_max_price(question)
    if max_price is not None:
        stmt = stmt.where(Package.price_try <= max_price)

    gb = detect_gb(question)
    if gb is not None:
        stmt = stmt.where(gb_band_filter(gb))

    # Semantic rank within the filtered set.
    query_vec = embed_query(question)
    distance = Package.embedding.cosine_distance(query_vec)
    # Nudge packages missing critical info slightly back without fully severing them
    # from semantic relevance. This way an operator-specific search still returns
    # results, and a close, complete alternative surfaces as the main recommendation.
    quality_adjusted_distance = distance + case(
        (Package.data_quality_status != "complete", 0.08), else_=0.0
    )
    stmt = stmt.order_by(quality_adjusted_distance).limit(k)

    results = list(session.scalars(stmt).all())

    # Fallback: if filters were too strict and nothing matched, drop the operator
    # and price/GB preferences and rank purely semantically so the user still gets
    # an answer. Eligibility filters stay -- they are correctness, not preference.
    if not results:
        loose = (
            select(Package)
            .where(*eligibility)
            .order_by(quality_adjusted_distance)
            .limit(k)
        )
        results = list(session.scalars(loose).all())

    return results
