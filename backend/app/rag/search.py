"""Structured tariff search.

Pipeline for one ``TariffQuery``:

1. SQL -- eligibility that is never relaxed: phone products only, customer type,
   activation channel, audience / age / institutional restrictions. One query returns
   the eligible rows with their BGE-M3 cosine distance (the catalog is ~100 rows, so no
   further SQL round trips are needed).
2. Python -- every user requirement is checked three-valued against the row's facts:
   True (confirmed), False (confirmed incompatible), None (the source does not say).
   Any False excludes the row; any None makes it an *unverified* candidate, ranked
   after all confirmed matches and labelled as such. Unknown is never read as a match.
3. Ranking -- an explicit objective ("en ucuz", "en çok internet", ...) sorts by that
   number with documented tie-breakers. Without one, a query with numeric or benefit
   requirements ranks the cheapest qualifying offer first (semantic distance breaks
   ties); a purely qualitative query ranks by semantic similarity.
4. No match -- nothing is relaxed silently. If no row satisfies everything, each
   relaxable requirement is dropped ONE at a time; the rows that then qualify are
   returned as *compromises*, labelled with the requirement they miss. Eligibility
   (age, audience, institution, channel, customer type) is never relaxed.

Prices are never rewritten. The comparison price is a separate, labelled value:
the ongoing price (after an introductory period; the no-commitment price when the
user refuses a commitment), and for prepaid packages its 30-day equivalent
``price * 30 / validity_days``.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from sqlalchemy import cast, not_, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.ingest.normalize import UNLIMITED, general_internet_gb
from app.models import Package
from app.rag.query import TariffQuery
from app.rag.retrieve import EXISTING_CUSTOMER_UNAVAILABLE

INF = float("inf")

MATCH, UNVERIFIED, COMPROMISE = "match", "unverified", "compromise"

_OPERATOR_TR = {"turkcell": "Turkcell", "vodafone": "Vodafone", "turk_telekom": "Türk Telekom"}


# ---------------------------------------------------------------- facts

def general_gb(p: Package) -> float:
    g = general_internet_gb(p.internet_gb, p.bonus_internet_gb, p.bonus_internet_scope)
    if g is None:
        return 0.0
    return INF if g == UNLIMITED else float(g)


def upfront_price(p: Package, q: TariffQuery) -> Optional[float]:
    """What is paid for one period on the basis the user asked for."""
    if q.no_commitment and p.commitment_status == "committed":
        return None if p.no_commitment_price_try is None else float(p.no_commitment_price_try)
    return None if p.price_try is None else float(p.price_try)


def ongoing_price(p: Package, q: TariffQuery) -> Optional[float]:
    """The price after an introductory period, so a one-month discount cannot win a
    long-term affordability question."""
    if p.promo_months and p.price_after_promo_try is not None and not (
            q.no_commitment and p.commitment_status == "committed"):
        return float(p.price_after_promo_try)
    return upfront_price(p, q)


def comparison_price(p: Package, q: TariffQuery) -> float:
    """30-day comparison price (equal to the monthly price for postpaid). Never
    shown as what is billed."""
    price = ongoing_price(p, q)
    if price is None:
        return INF
    if p.line_type == "prepaid" and p.validity_days:
        return price * 30 / p.validity_days
    return price


def _benefits(p: Package) -> list[dict]:
    return p.benefits or []


def _app_free(p: Package, app: str) -> Optional[bool]:
    social = [a.lower() for a in (p.social_media_apps or [])]
    if p.social_media_gb and app in social:
        return True
    for b in _benefits(p):
        if b.get("category") == "app_data" and b.get("offer") == "included" and app in [
                a.lower() for a in (b.get("apps") or [])]:
            return True
    if p.social_media_gb and not p.social_media_apps:
        return None  # a social quota whose apps the source does not list
    if p.benefits_status == "unverified":
        return None
    return False


def _subscription(p: Package, service: str, trials_ok: bool) -> Optional[bool]:
    offers = {"included", "free_trial"} if trials_ok else {"included"}
    for b in _benefits(p):
        if b.get("category") == "subscription" and b.get("offer") in offers and service in b.get("service", "").lower():
            return True
    return None if p.benefits_status == "unverified" else False


def _roaming(p: Package) -> Optional[bool]:
    if any(b.get("category") == "roaming" and b.get("offer") == "included" for b in _benefits(p)):
        return True
    return None if p.benefits_status == "unverified" else False


def _at_least(value, need) -> Optional[bool]:
    if value is None:
        return None
    return value == UNLIMITED or value >= need


# ---------------------------------------------------------------- constraints

@dataclass(frozen=True)
class Constraint:
    key: str
    label: str                      # Turkish, for notes and compromise labels
    check: Callable[[Package], Optional[bool]]
    relaxable: bool = True


def _tl(v: float) -> str:
    return f"{v:g} TL"


def constraints(q: TariffQuery) -> list[Constraint]:
    out: list[Constraint] = []
    if q.operators:
        names = " / ".join(_OPERATOR_TR[o] for o in sorted(q.operators))
        out.append(Constraint("operator", f"operatör {names}", lambda p: p.operator in q.operators))
    if q.line_type:
        label = "faturasız" if q.line_type == "prepaid" else "faturalı"
        out.append(Constraint("line_type", label, lambda p: p.line_type == q.line_type))
    if q.max_price is not None:
        def under(p):
            long_prepaid = p.line_type == "prepaid" and (p.validity_days or 0) > 31
            price = comparison_price(p, q) if (q.monthly_budget and long_prepaid) else ongoing_price(p, q)
            return None if price is None or price == INF else price <= q.max_price
        out.append(Constraint("max_price", f"en fazla {_tl(q.max_price)}", under))
    if q.min_price is not None:
        out.append(Constraint("min_price", f"en az {_tl(q.min_price)}",
                              lambda p: None if upfront_price(p, q) is None else upfront_price(p, q) >= q.min_price))
    if q.unlimited_internet:
        out.append(Constraint("unlimited_internet", "sınırsız genel internet", lambda p: general_gb(p) == INF))
    if q.min_gb is not None:
        out.append(Constraint("min_gb", f"en az {q.min_gb:g} GB genel internet",
                              lambda p: None if p.internet_gb is None else general_gb(p) >= q.min_gb))
    if q.max_gb is not None:
        out.append(Constraint("max_gb", f"en fazla {q.max_gb:g} GB",
                              lambda p: None if p.internet_gb is None else general_gb(p) <= q.max_gb))
    if q.unlimited_social:
        out.append(Constraint("unlimited_social", "sınırsız sosyal medya", lambda p: p.social_media_gb == UNLIMITED))
    for app in sorted(q.apps or ()):
        out.append(Constraint(f"app:{app}", f"{app} trafiği genel kotadan düşmesin",
                              lambda p, a=app: _app_free(p, a)))
    for service in sorted(q.subscriptions or ()):
        kind = "dahil (deneme değil)" if q.no_trials else "hediye/deneme"
        out.append(Constraint(f"subscription:{service}", f"{service} üyeliği {kind}",
                              lambda p, s=service: _subscription(p, s, not q.no_trials)))
    if q.roaming:
        out.append(Constraint("roaming", "yurt dışı kullanım avantajı", _roaming))
    if q.unlimited_minutes:
        out.append(Constraint("unlimited_minutes", "sınırsız konuşma",
                              lambda p: None if p.voice_minutes is None else p.voice_minutes == UNLIMITED))
    elif q.min_minutes is not None:
        out.append(Constraint("min_minutes", f"en az {q.min_minutes} dakika",
                              lambda p: _at_least(p.voice_minutes, q.min_minutes)))
    if q.min_sms is not None:
        out.append(Constraint("min_sms", f"en az {q.min_sms} SMS", lambda p: _at_least(p.sms_count, q.min_sms)))
    if q.no_commitment:
        def no_commit(p):
            if p.commitment_status == "no_commitment":
                return True
            if p.commitment_status == "committed":
                return True if p.no_commitment_price_try is not None else None
            return None
        out.append(Constraint("no_commitment", "taahhütsüz", no_commit))
    if q.renewal:
        label = "otomatik yenilenmeyen (tek seferlik)" if q.renewal == "one_time" else "otomatik yenilenen"
        out.append(Constraint("renewal", label,
                              lambda p: None if p.renewal_type == "unknown" else p.renewal_type == q.renewal))
    # Eligibility: rows confirmed incompatible are already gone in SQL; these only
    # separate confirmed from unknown, and are never relaxed.
    if q.channel:
        label = "numara taşıma" if q.channel == "number_porting" else "yeni hat"
        out.append(Constraint("channel", f"{label} ile alınabilir",
                              lambda p: None if p.activation_channels is None else q.channel in p.activation_channels,
                              relaxable=False))
    out.append(Constraint("province", "il kısıtı (kullanıcının ili bilinmiyor)",
                          lambda p: None if "province_restricted" in (p.eligibility_tags or []) else True,
                          relaxable=False))
    return out


def _evaluate(p: Package, cons: list[Constraint]) -> tuple[list[Constraint], list[Constraint]]:
    failed, unknown = [], []
    for c in cons:
        result = c.check(p)
        if result is False:
            failed.append(c)
        elif result is None:
            unknown.append(c)
    return failed, unknown


# ---------------------------------------------------------------- SQL eligibility

def eligibility_filters(q: TariffQuery, *, include_customer_type: bool = True) -> list:
    """Who CAN buy the package. Never relaxed for a better-looking answer."""
    filters = [Package.product_scope == "mobile_voice"]
    if include_customer_type and q.customer_type:
        filters.append(Package.customer_type.in_([q.customer_type, "any"]))
    if q.channel:
        # Exclude only rows confirmed to the OTHER channel; unknown stays (flagged later).
        channels = cast(Package.activation_channels, JSONB)
        other = "new_line" if q.channel == "number_porting" else "number_porting"
        filters.append(or_(Package.activation_channels.is_(None),
                           not_(channels.has_key(other) & not_(channels.has_key(q.channel)))))
    tags = cast(Package.eligibility_tags, JSONB)
    if q.audience == "child":
        filters.append(Package.audience_segment == "child")
    elif q.audience in ("youth", "family", "public_special"):
        filters.append(Package.audience_segment.in_([q.audience, "general"]))
    elif q.age is not None:
        filters.append(Package.audience_segment.in_(["general", "youth", "child"]))
    else:
        # No age or audience given: a youth / child / family / institutional offer is
        # not assumed to be open to the user (see youth_hint for the follow-up).
        filters.append(Package.audience_segment == "general")
    if q.audience != "public_special":
        filters.append(or_(Package.eligibility_tags.is_(None), not_(tags.has_key("parliamentarian"))))
    if q.age is not None:
        filters.append(or_(Package.age_min.is_(None), Package.age_min <= q.age))
        filters.append(or_(Package.age_max.is_(None), Package.age_max >= q.age))
    return filters


def _candidates(session: Session, filters: list, query_vec) -> list[tuple[Package, float]]:
    distance = Package.embedding.cosine_distance(query_vec).label("distance")
    return [(p, float(d)) for p, d in session.execute(select(Package, distance).where(*filters)).all()]


# ---------------------------------------------------------------- ranking

def _price_key(p: Package, q: TariffQuery) -> float:
    return comparison_price(p, q)


def _included_benefits(p: Package) -> int:
    return sum(1 for b in _benefits(p) if b.get("offer") == "included")


def rank(rows: list[tuple[Package, float]], q: TariffQuery) -> list[tuple[Package, float]]:
    """Deterministic order. Tie-breakers always end with the name, so equal keys
    never depend on database order."""
    obj = q.objective
    if obj == "cheapest":
        key = lambda r: (_price_key(r[0], q), -general_gb(r[0]), r[1], r[0].name)  # noqa: E731
    elif obj == "most_data":
        key = lambda r: (-general_gb(r[0]), _price_key(r[0], q), r[1], r[0].name)  # noqa: E731
    elif obj == "longest_validity":
        key = lambda r: (-(r[0].validity_days or 30), _price_key(r[0], q), r[1], r[0].name)  # noqa: E731
    elif obj == "most_benefits":
        key = lambda r: (-_included_benefits(r[0]), _price_key(r[0], q), r[1], r[0].name)  # noqa: E731
    elif obj == "best_value":
        finite = [general_gb(p) for p, _ in rows if general_gb(p) != INF] or [0.0]
        cap = max(finite)  # unlimited counts as the largest finite allowance on offer

        def key(r):
            gb = general_gb(r[0])
            gb = cap if gb == INF else gb
            price = _price_key(r[0], q)
            return (-(gb / price) if price not in (0, INF) else 0, price, r[1], r[0].name)
    elif _has_hard_numbers(q):
        key = lambda r: (_price_key(r[0], q), r[1], r[0].name)  # noqa: E731
    else:
        key = lambda r: (r[1], r[0].name)  # noqa: E731
    return sorted(rows, key=key)


def _has_hard_numbers(q: TariffQuery) -> bool:
    return any(v is not None for v in (q.max_price, q.min_price, q.min_gb, q.min_minutes, q.unlimited_internet,
                                       q.apps, q.subscriptions, q.unlimited_social))


# ---------------------------------------------------------------- search

@dataclass
class SearchResult:
    packages: list[Package]
    notes: list[str] = field(default_factory=list)


def _flags(p: Package, q: TariffQuery, status: str, missing: list[Constraint]) -> list[str]:
    flags = []
    if status == UNVERIFIED:
        flags.append("DOGRULANAMADI: kaynak su sartlari belirtmiyor -> " + "; ".join(c.label for c in missing)
                     + ". Bunu uygun diye sunma, operatorden teyit edilmesini soyle.")
    elif status == COMPROMISE:
        flags.append("UZLASMA: kullanicinin su sartini KARSILAMIYOR -> " + "; ".join(c.label for c in missing)
                     + ". Yalnizca acikca etiketlenmis bir alternatif olarak sun.")
    upfront = upfront_price(p, q)
    if q.no_commitment and p.commitment_status == "committed" and upfront is not None:
        flags.append(f"Kullanici taahhut istemiyor: gecerli fiyat taahhutsuz fiyat {upfront:g} TL (aylik).")
    if p.line_type == "prepaid" and p.validity_days and p.validity_days != 30 and upfront is not None:
        flags.append(f"Kiyas icin 30 gunluk karsiligi ~{comparison_price(p, q):.0f} TL; bu bir faturalama "
                     f"degildir, paket {upfront:g} TL'ye tek seferde {p.validity_days} gun icin alinir.")
    if p.promo_months and p.price_after_promo_try is not None:
        flags.append(f"Siralama tanitim sonrasi {float(p.price_after_promo_try):g} TL uzerinden yapildi.")
    return flags


def _annotate(p: Package, q: TariffQuery, status: str, missing: list[Constraint]) -> Package:
    # Transient, non-mapped attributes read by the prompt and the benchmark.
    p.retrieval_status = status
    p.retrieval_flags = _flags(p, q, status, missing)
    return p


def search(session: Session, q: TariffQuery, query_vec, k: int) -> SearchResult:
    notes: list[str] = []
    rows = _candidates(session, eligibility_filters(q), query_vec)
    if not rows and q.customer_type == "existing":
        # The catalog holds new-customer offers only: show them, flagged, instead of nothing.
        rows = _candidates(session, eligibility_filters(q, include_customer_type=False), query_vec)
        if rows:
            notes.append(EXISTING_CUSTOMER_UNAVAILABLE)

    cons = constraints(q)
    matched, unverified = [], []
    for row in rows:
        failed, unknown = _evaluate(row[0], cons)
        if not failed:
            (unverified if unknown else matched).append((row, unknown))
    result = [_annotate(p, q, MATCH, []) for p, _d in rank([r for r, _ in matched], q)]
    unknown_by_id = {id(r[0]): u for r, u in unverified}
    for p, d in rank([r for r, _ in unverified], q):
        if len(result) >= k:
            break
        result.append(_annotate(p, q, UNVERIFIED, unknown_by_id[id(p)]))
    result = result[:k]

    if not matched and unverified:
        notes.append("Kullanicinin tum sartlarini KAYNAKTA DOGRULANMIS bicimde karsilayan paket yok. Asagidaki "
                     "kaynaklarin her biri en az bir sart icin 'kaynakta belirtilmemis' durumunda (satirlarindaki "
                     "DOGRULANAMADI notuna bak). Bunu ilk cumlede soyle; bu paketleri kesin uygun diye sunma.")
    if not matched and not unverified:
        result, relax_note = _compromises(rows, cons, q, k)
        notes.append(relax_note)
    if not result:
        return SearchResult([], notes)
    for p in result:
        # Per-requirement verdicts for the answer context (rag.facts); the province
        # row is shown only where it matters.
        p.retrieval_checks = [(c.label, c.check(p)) for c in cons if c.key != "province" or c.check(p) is None]
    notes.append(ranking_note(q))
    hint = _youth_hint(session, q, cons, query_vec)
    if hint:
        notes.append(hint)
    return SearchResult(result, notes)


_RANKING_TEXT = {
    "cheapest": "en dusuk fiyat (faturasizda 30 gunluk karsilik, tanitim sonrasi fiyat)",
    "most_data": "en cok genel kullanim interneti (sosyal medya/uygulama kotasi sayilmaz), esitlikte fiyat",
    "best_value": "genel internet GB basina en dusuk 30 gunluk fiyat",
    "longest_validity": "en uzun gecerlilik suresi, esitlikte fiyat",
    "most_benefits": "resmi sayfada DAHIL olan avantaj sayisi (deneme ve satin alma gerektirenler sayilmaz)",
}


def ranking_note(q: TariffQuery) -> str:
    """Why the sources are in this order -- the answer must not invent other reasons."""
    if q.objective in _RANKING_TEXT:
        basis = _RANKING_TEXT[q.objective]
    elif _has_hard_numbers(q):
        basis = "sartlari karsilayanlar arasinda en dusuk fiyat (faturasizda 30 gunluk karsilik)"
    else:
        basis = "sorunun anlamsal benzerligi"
    return (f"Kaynaklar su olcute gore siralandi: {basis}. Oneriyi bu olcutle ve ESLESME satirlariyla gerekcelendir; "
            "kapsama, sebeke kalitesi veya 'en iyi' gibi kaynakta olmayan gerekce kullanma.")


def _compromises(rows, cons: list[Constraint], q: TariffQuery, k: int) -> tuple[list[Package], str]:
    """Drop one relaxable requirement at a time; label every row with what it misses."""
    options: list[tuple[Constraint, list]] = []
    for c in cons:
        if not c.relaxable:
            continue
        others = [o for o in cons if o is not c]
        fits = [r for r in rows if not _evaluate(r[0], others)[0] and c.check(r[0]) is False]
        if fits:
            options.append((c, rank(fits, q)))
    blocking = [c.label for c in cons if c.relaxable]
    if not options:
        return [], ("Kullanicinin sartlarinin TAMAMINI karsilayan paket katalogda YOK ve tek bir sarti gevsetmek de "
                    "sonuc vermiyor (sartlar: " + "; ".join(blocking) + "). Paket onermeden bunu acikca soyle ve "
                    "hangi sartin esneyebilecegini sor.")
    picked: list[Package] = []
    seen = set()
    per_option = max(1, k // len(options))
    for c, ranked in options:
        for p, _d in ranked[:per_option]:
            if p.id not in seen and len(picked) < k:
                seen.add(p.id)
                picked.append(_annotate(p, q, COMPROMISE, [c]))
    which = "; ".join(f"'{c.label}' sartindan vazgecilirse {len(r)} paket" for c, r in options)
    return picked, ("Kullanicinin sartlarinin TAMAMINI karsilayan paket katalogda YOK. Ilk cumlede bunu ve hangi "
                    "sartin engel oldugunu acikca soyle. Tek bir sart gevsetildiginde: " + which + ". Asagidaki "
                    "kaynaklar YALNIZCA bu tur uzlasma alternatifleridir (satirlarindaki UZLASMA notu hangi sarti "
                    "karsilamadigini soyler); hicbirini sartlara uyan bir paket gibi sunma. Yas, kurum veya kanal "
                    "uygunlugu hicbir zaman gevsetilmedi.")


def _youth_hint(session: Session, q: TariffQuery, cons: list[Constraint], query_vec) -> Optional[str]:
    """No age given: say (without citing them) that age-restricted offers also qualify."""
    if q.age is not None or q.audience is not None:
        return None
    youth = replace(q, audience="youth")
    rows = [r for r in _candidates(session, eligibility_filters(youth), query_vec) if r[0].audience_segment == "youth"]
    fits = [r for r in rows if not _evaluate(r[0], cons)[0]]
    if not fits:
        return None
    ages = sorted({p.age_max for p, _ in fits if p.age_max})
    limit = f"{ages[-1]} yas ve alti" if ages else "gencler"
    return (f"Kullanici yasini belirtmedi; bu yuzden yalnizca herkese acik paketler kaynak olarak verildi. "
            f"{limit} icin sartlara uyan {len(fits)} genc tarifesi daha var. Yanitin sonunda tek cumleyle yasini "
            f"sorabilirsin; bu paketlerin adini veya fiyatini soyleme (kaynak verilmedi).")




def pinned(session: Session, names: list[str], q: TariffQuery) -> list[Package]:
    """Rows the user named or pointed at in an earlier answer, checked against the
    current requirements like any other row (a named tariff that misses a requirement
    is labelled, never passed off as a match)."""
    if not names:
        return []
    rows = session.scalars(select(Package).where(Package.product_scope == "mobile_voice",
                                                 Package.name.in_(names))).all()
    by_name = {p.name: p for p in rows}
    cons = constraints(q)
    out = []
    for name in names:
        p = by_name.get(name)
        if p is None:
            continue
        failed, unknown = _evaluate(p, cons)
        status = COMPROMISE if failed else (UNVERIFIED if unknown else MATCH)
        _annotate(p, q, status, failed or unknown)
        p.retrieval_checks = [(c.label, c.check(p)) for c in cons if c.key != "province" or c.check(p) is None]
        out.append(p)
    return out
