"""Hybrid retrieval: parse hard constraints from the question into SQL filters,
then rank the filtered set by pgvector cosine similarity to the query embedding.
Returns full typed Package rows so the API can render exact comparison tables
and ground citations on real values.
"""
import re
from typing import Optional

from sqlalchemy import and_, cast, not_, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest.normalize import UNLIMITED
from app.models import Package
from app.rag.embed import embed_query

def tr_lower(text: str) -> str:
    """Turkish-aware lowercase: 'I' -> 'ı' and 'İ' -> 'i' (str.lower would turn
    'İ' into 'i' + a combining dot, which breaks plain-letter regexes)."""
    return text.replace("I", "ı").replace("İ", "i").lower()


# Operator aliases -> canonical key. Matched on word boundaries: a bare substring
# test made "tt" fire inside ordinary words ("hattım", "attığım", "ITTIFAK") and
# silently pinned the query to Türk Telekom. A Turkish suffix after an apostrophe
# ("Turkcell'e", "TT'den") still matches because the apostrophe ends the word.
_OPERATOR_PATTERNS = [
    (re.compile(r"\bturkcell"), "turkcell"),
    (re.compile(r"\bvodafone"), "vodafone"),
    (re.compile(r"\bt[uü]rk ?telekom"), "turk_telekom"),
    (re.compile(r"\btt\b"), "turk_telekom"),
]


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
    low = tr_lower(q)
    for pattern, canonical in _OPERATOR_PATTERNS:
        if pattern.search(low):
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


# A number in a price context: Turkish thousands separator "1.000" and decimal
# comma "99,90" are both accepted.
_NUMBER_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?(?!\d)")
_CURRENCY_RE = re.compile(r"\s*(?:tl\b|₺|lira)['’]?[a-zçğıöşü]*")
# Units that make a number NOT a price ("26 yaş", "3 ay", "1000 dakika", "20 GB").
_NON_PRICE_UNIT_RE = re.compile(
    r"\s*['’]?\s*(?:gb|mb|ya[sş]|ay\b|ayl[ıi]k|ayd[ae]|g[uü]n|hafta|y[ıi]l|"
    r"dk\b|dakika|sms|mesaj|saniye|ki[sş]i|hat\b|g\b)"
)
# Bound words, looked for right after the amount ("200 TL'ye kadar", "500 TL altı",
# "100 TL'den fazla") or right before it ("en fazla 150 TL", "en az 100 TL").
# "150 TL en fazla" / "150 TL en az" are handled first so "en" is not mistaken for
# a case suffix ("TL'den az") and flipped.
# "300 liradan fazla vermem", "300 TL üstü olmasın": a negated minimum is a ceiling.
_NEGATED_MIN = (r"['’]?[a-zçğıöşü]*\s*(?:fazla|[uü]st[uü]|[uü]zeri)\w*\s+(?:vermem|veremem|[oö]demem|[oö]deyemem|"
                r"harcamam|harcayamam|istemem|olmas[ıi]n|olmamal[ıi]|vermek istemiyorum|[oö]demek istemiyorum)")
_MAX_AFTER_RE = re.compile(
    r"^\s*(?:" + _NEGATED_MIN + r"|en fazla\b|(?!en az\b)['’]?[a-zçğıöşü]*\s*"
    r"(?:kadar|alt|ge[cç]mesin|a[sş]mas[ıi]n|az\b|ucuz|d[uü][sş][uü]k))"
)
_MIN_AFTER_RE = re.compile(
    r"^\s*(?:en az\b|(?!en fazla\b)['’]?[a-zçğıöşü]*\s*(?:fazla|[uü]st|[uü]zeri|pahal))"
)
_MAX_BEFORE_RE = re.compile(r"(?:en fazla|maksimum|max|azami|under|below)\s*$")
_MIN_BEFORE_RE = re.compile(r"(?:en az|minimum|min|asgari|en d[uü][sş][uü]k)\s*$")
# Any upper-bound word anywhere in the query (used only for the fallback below).
_ANY_MAX_WORD_RE = re.compile(r"alt[ıi]|under|below|kadar|en fazla|maksimum|\bmax\b")


def _amount(m: re.Match) -> float:
    whole = m.group(1).replace(".", "")
    return float(f"{whole}.{m.group(2)}") if m.group(2) else float(whole)


def detect_price_bounds(q: str) -> tuple[Optional[float], Optional[float]]:
    """Return (min_price, max_price) in TL from a Turkish query.

    Only an amount that is clearly a price counts: one marked with TL/lira/₺, or a
    bare number directly bound by a price word ("200'e kadar"). Ages, durations,
    minutes, SMS and GB amounts are never prices.
    """
    low = tr_lower(q)
    lo: Optional[float] = None
    hi: Optional[float] = None
    first_money: Optional[float] = None
    for m in _NUMBER_RE.finditer(low):
        rest = low[m.end():]
        if _NON_PRICE_UNIT_RE.match(rest):
            continue
        currency = _CURRENCY_RE.match(rest)
        after = rest[currency.end():] if currency else rest
        before = low[max(0, m.start() - 20):m.start()]
        if _MIN_BEFORE_RE.search(before):
            bound = "min"
        elif _MAX_BEFORE_RE.search(before):
            bound = "max"
        elif _MAX_AFTER_RE.match(after):
            bound = "max"
        elif _MIN_AFTER_RE.match(after):
            bound = "min"
        else:
            bound = None
        if not currency and bound is None:
            continue
        value = _amount(m)
        if bound == "max" and hi is None:
            hi = value
        elif bound == "min" and lo is None:
            lo = value
        elif bound is None and first_money is None:
            first_money = value
    # "20 GB altında 150 TL paket": the bound word sits next to another quantity,
    # but the only price in the query is clearly meant as the ceiling.
    if lo is None and hi is None and first_money is not None and _ANY_MAX_WORD_RE.search(low):
        hi = first_money
    return lo, hi


def detect_max_price(q: str) -> Optional[float]:
    return detect_price_bounds(q)[1]


def detect_min_price(q: str) -> Optional[float]:
    return detect_price_bounds(q)[0]


def detect_gb(q: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*gb", q.lower())
    return float(m.group(1).replace(",", ".")) if m else None


def gb_band_filter(gb: float, tolerance: float = 5):
    """GB is a soft target: keep packages within +/- 5 GB of the ask so "compare
    20 GB packages" returns the relevant band, not exact matches only. Measured on
    general-use internet (a general-scope bonus counts; social, hotspot-only and app-only
    quotas do not). Unlimited general internet satisfies any GB request."""
    general = Package.general_internet_gb
    return or_(general == UNLIMITED, (general >= gb - tolerance) & (general <= gb + tolerance))


# "numaramı taşıyacağım / taşırsam / taşıyorum": any inflection after "numara... taşı".
_PORTING_RE = re.compile(r"numara\w*\s+ta[sş][ıi]|ta[sş][ıi]ma\b|ta[sş][ıi]yarak|ta[sş][ıi]mak")
_NEW_LINE_RE = re.compile(r"yeni\s+hat")


def detect_activation_channel(q: str) -> Optional[str]:
    """'number_porting' / 'new_line' when the question names exactly one channel."""
    t = tr_lower(q)
    porting, new_line = bool(_PORTING_RE.search(t)), bool(_NEW_LINE_RE.search(t))
    if porting == new_line:
        return None
    return "number_porting" if porting else "new_line"


def _channel_filter(channel: str):
    """Exclude packages the source restricts to the OTHER channel. Packages whose
    channel is unknown stay in: the prompt says the channel is unspecified."""
    channels = cast(Package.activation_channels, JSONB)
    other = "new_line" if channel == "number_porting" else "number_porting"
    only_other = and_(channels.has_key(other), not_(channels.has_key(channel)))
    return or_(Package.activation_channels.is_(None), not_(only_other))


def _eligibility_filters(question: str, *, include_customer_type: bool = True) -> list:
    """Filters for the "who CAN buy which package" question. Unlike price/GB
    preferences, these are not dropped in the fallback: a postpaid package must not
    be offered to someone asking for prepaid, nor an existing-only package to a new
    customer."""
    # Non-phone device/data products can never enter RAG for any query.
    filters = [Package.product_scope == "mobile_voice"]
    line_type = detect_line_type(question)
    if line_type:
        filters.append(Package.line_type == line_type)

    customer_type = detect_customer_type(question) if include_customer_type else None
    if customer_type:
        # "any" packages are open to both customer types.
        filters.append(Package.customer_type.in_([customer_type, "any"]))
    channel = detect_activation_channel(question)
    if channel:
        # A number-porting customer must not be sold a new-line-only offer (and vice versa).
        filters.append(_channel_filter(channel))
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


# Retrieval notes: facts about HOW the result set was built that the answer must
# state. They travel as trusted pipeline data, never as user input.
EXISTING_CUSTOMER_UNAVAILABLE = "existing_customer_unavailable"


def retrieve_with_notes(
    session: Session, question: str, history=None, k: Optional[int] = None
) -> tuple[list[Package], list[str]]:
    """Parse the conversation into a TariffQuery (rag.query),
    then filter, check and rank structurally (rag.search). The embedding covers the
    recent user turns too, so a follow-up like "peki en ucuzu?" keeps its topic."""
    from app.rag.history import contextual_question
    from app.rag.query import conversation_query
    from app.rag.search import search

    from app.rag.references import resolve
    from app.rag.search import pinned

    query = conversation_query(question, history)
    query_vec = embed_query(contextual_question(question, history))
    k = k or settings.top_k
    result = search(session, query, query_vec, k)
    names, ref_note = resolve(question, history)
    named = pinned(session, names, query)
    notes = result.notes + ([ref_note] if ref_note else [])
    if named:
        # Named / referenced tariffs lead; the rest of the ranked list follows.
        seen = {p.id for p in named}
        packages = named + [p for p in result.packages if p.id not in seen]
        notes.append("Kullanici su tarife(ler)e adiyla veya onceki cevaba atifla isaret etti: "
                     + ", ".join(p.name for p in named) + ". Cevabi once bunlara odakla.")
        return packages[:k], notes
    return result.packages, notes


def retrieve(session: Session, question: str, history=None, k: Optional[int] = None) -> list[Package]:
    return retrieve_with_notes(session, question, history, k)[0]
