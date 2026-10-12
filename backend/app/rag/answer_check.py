"""Post-generation guard: the LLM writes the prose, but commercial claims
must match the facts it was given.

``guard`` checks the answer against the sources sent to the model, with targeted
checks for the claims that do real damage when wrong:

* citation ids that do not exist, and any URL (the UI renders the real links);
* money amounts ("X TL") and data amounts ("X GB") that are not a value of the
  sources, a TarifeAI 30-day figure, or a number the user wrote;
* "sınırsız (genel) internet" when no source has unlimited general internet;
* a free trial presented as an included / free / lifelong subscription;
* a compromise or unverified source cited without saying what it misses.

A failing sentence is removed (a compromise sentence is completed with a fixed
caveat instead). If too much is removed, or nothing cited survives, the answer is
replaced by ``fallback_answer``, rendered from the facts alone. One pass, no loops,
no second LLM call. Checks are per sentence and conservative: they only look for
specific numeric / categorical claims, not general meaning.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.ingest.normalize import UNLIMITED, internet_phrase
from app.models import Package
from app.rag.facts import OPERATOR_TR, fmt_tl, period_label, price_30d

logger = logging.getLogger(__name__)

_MARKER_RE = re.compile(r"\[(S\d+)(?:\s*[–-]\s*(S\d+))?\]")
_URL_RE = re.compile(r"\(?(?:https?://|www\.)[^\s)\]>]+\)?", re.I)
_TL_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?\s*(?:TL\b|₺|lira)", re.I)
_GB_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*GB\b", re.I)
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
# "sınırsız (genel) internet", "genel internet sınırsız", "interneti tamamen sınırsız",
# "tüm internet kullanımın sınırsız": an unlimited claim tied to the word internet.
_UNLIMITED_GENERAL_RE = re.compile(
    r"s[ıi]n[ıi]rs[ıi]z\s+(?:genel\s+)?internet|internet\w*\s+(?:\S+\s+){0,2}s[ıi]n[ıi]rs[ıi]z")
_NEGATION_RE = re.compile(r"de[gğ]il|yok\b|bulunm|sunmuyor|olmayan|olmad|i[cç]ermiyor|veren .{0,20}yok")
_LIFELONG_RE = re.compile(r"[oö]m[uü]r\s*boyu|s[uü]resiz|kal[ıi]c[ıi]\s+olarak|sonsuza|hep\s+[uü]cretsiz")
_FREE_RE = re.compile(r"hediye|[uü]cretsiz|bedava|dahil")
_HEDGE_RE = re.compile(r"kar[sş][ıi]lam[ıi]yor|kar[sş][ıi]lamad|kar[sş][ıi]lamayan|a[sş]an\b|[sş]art[ıi]n[ıi]|uzla[sş]ma|a[sş][ıi]yor|de[gğ]il|teyit|do[gğ]rula|belirtilmemi[sş]|"
                       r"bilinmiyor|esnet|ama\b|ancak|fakat|yaln[ıi]zca|[uü]st[uü]nde|alt[ıi]nda")
_NO_MATCH_RE = re.compile(r"\byok\b|bulunam|bulamad|kar[sş][ıi]layan .{0,40}(?:yok|bulun)|mevcut de[gğ]il")

# A restricted quota named right before the claim is not a general claim
# ("sosyal medya uygulamalarında sınırsız internet", "WhatsApp'ta sınırsız internet").
_APPS = ("whatsapp", "instagram", "youtube", "tiktok", "netflix", "bip", "yaay", "facebook", "messenger",
         "spotify", "chatgpt", "claude", "twitter", "amazon", "e-dergi")
_RESTRICTED_CONTEXT_RE = re.compile(r"(?:sosyal|uygulama|mesajla[sş]ma|payla[sş][ıi]m|hotspot|"
                                    + "|".join(_APPS) + r"|\bx\b)\S*\s+(?:\S+\s+){0,3}$")
_GENERIC_RESTRICTED_RE = re.compile(r"sosyal|uygulama|mesajla[sş]ma|payla[sş][ıi]m")


def _unlimited_claims(low: str) -> tuple[bool, str | None]:
    """(general claim?, restricted context of the first app-scoped claim or None)."""
    general, restricted = False, None
    for m in _UNLIMITED_GENERAL_RE.finditer(low):
        # Negation only counts in the claim's own clause ("sınırsız internet yok",
        # "interneti sınırsız değil"), not in a neighbouring one ("kota derdi yok, ...").
        if _NEGATION_RE.search(re.split(r"[,;:]", low[m.end():m.end() + 30])[0]):
            continue
        ctx = _RESTRICTED_CONTEXT_RE.search(low[max(0, m.start() - 60):m.start()])
        if "genel" in m.group(0) or "genel" in low[max(0, m.start() - 12):m.start()] or not ctx:
            general = True
        elif restricted is None:
            restricted = ctx.group(0)
    return general, restricted


def _unlimited_apps(p: Package) -> tuple[set[str], bool]:
    """(apps with unlimited app-scoped data, any unlimited app-scoped data at all?)."""
    apps: set[str] = set()
    any_ = False
    if p.social_media_gb == UNLIMITED:
        any_ = True
        apps |= {_fold(a) for a in p.social_media_apps or []}
    for b in p.benefits or []:
        if b.get("category") == "app_data" and b.get("amount_gb") == UNLIMITED and b.get("offer") == "included":
            any_ = True
            apps |= {_fold(a) for a in b.get("apps") or []}
    return apps, any_


def _restricted_claim_supported(ctx: str, low: str, scope: list[Package]) -> bool:
    named = {a for a in _APPS if re.search(rf"(?<!\w){re.escape(a)}", low)}
    for p in scope:
        apps, any_ = _unlimited_apps(p)
        if named and all(any(n in a for a in apps) for n in named):
            return True
        if not named and any_ and (_GENERIC_RESTRICTED_RE.search(ctx) or "hotspot" not in ctx):
            return True
    return False


_OPERATOR_NAME_RE = {
    "turkcell": re.compile(r"turkcell"),
    "vodafone": re.compile(r"vodafone"),
    "turk_telekom": re.compile(r"t[uü]rk\s*telekom|\btt\b"),
}
_CHEAPEST_RE = re.compile(r"\ben\s+(?:ucuz|uygun\s+fiyatl|d[uü][sş][uü]k\s+(?:fiyat|[uü]cret|maliyet))")
_MOST_DATA_RE = re.compile(r"\ben\s+(?:[cç]ok|fazla|y[uü]ksek)\s+(?:genel\s+)?internet")
_CHEAPER_RE = re.compile(r"daha\s+(?:ucuz|uygun\s+fiyat|d[uü][sş][uü]k\s+fiyat)")
_PRICIER_RE = re.compile(r"daha\s+pahal")
_MORE_DATA_RE = re.compile(r"daha\s+(?:[cç]ok|fazla|bol)\s+(?:genel\s+)?internet")
_LESS_DATA_RE = re.compile(r"daha\s+az\s+(?:genel\s+)?internet")


def _basis(p: Package) -> float | None:
    """The 30-day comparison basis used by the <KIYAS> block."""
    if p.price_try is None:
        return None
    if p.line_type == "postpaid":
        return float(p.price_try)
    return float(p.price_try) * 30 / p.validity_days if p.validity_days else None


def _data(p: Package) -> float | None:
    from app.rag.facts import general_gb_value
    g = general_gb_value(p)
    return float("inf") if g == UNLIMITED else g


def _comparison_problem(low: str, cited: list[Package], packages: list[Package]) -> str | None:
    """Superlatives and two-way comparatives must agree with the deterministic basis.
    Only checked when exactly one source is cited and the claim is unambiguous."""
    if len(cited) != 1 or len(packages) < 2 or _NEGATION_RE.search(low):
        return None
    p = cited[0]
    others = [o for o in packages if o is not p]
    for regex, value, best in ((_CHEAPEST_RE, _basis, min), (_MOST_DATA_RE, _data, max)):
        if regex.search(low):
            vals = [value(o) for o in packages]
            if value(p) is not None and None not in vals and value(p) != best(vals):
                return "superlative contradicts the comparison"
    if len(others) == 1:
        o = others[0]
        for regex, value, sign in ((_CHEAPER_RE, _basis, -1), (_PRICIER_RE, _basis, 1),
                                   (_MORE_DATA_RE, _data, 1), (_LESS_DATA_RE, _data, -1)):
            if regex.search(low) and value(p) is not None and value(o) is not None \
                    and (value(p) - value(o)) * sign <= 0:
                return "comparison contradicts the facts"
    return None


# Share of sentences that may be dropped before the whole answer is replaced.
MAX_DROPPED_SHARE = 0.4


@dataclass
class GuardResult:
    answer: str
    violations: list[str] = field(default_factory=list)
    fallback: bool = False


def _fold(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def _numbers(text: str) -> set[float]:
    out = set()
    for n in _NUM_RE.findall(text or ""):
        try:
            out.add(float(n.replace(".", "").replace(",", ".")))
        except ValueError:
            pass
    return out


def _money_facts(p: Package) -> set[float]:
    out = set()
    for v in (p.price_try, p.no_commitment_price_try, p.price_after_promo_try):
        if v is not None:
            out.add(float(v))
    calc = price_30d(p)
    if calc is not None:
        out.add(float(calc))
    for b in p.benefits or []:
        for k in ("value_try", "after_trial_price_try"):
            if b.get(k) is not None:
                out.add(float(b[k]))
        out |= _numbers(b.get("service", "")) | _numbers(b.get("note") or "")
    return out | _numbers(p.conditions or "") | _numbers(p.campaign or "")


def _gb_facts(p: Package) -> set[float]:
    from app.ingest.normalize import general_internet_gb

    out = {float(v) for v in (p.internet_gb, p.social_media_gb, p.bonus_internet_gb) if v not in (None, UNLIMITED)}
    g = general_internet_gb(p.internet_gb, p.bonus_internet_gb, p.bonus_internet_scope)
    if g not in (None, UNLIMITED):
        out.add(float(g))
    for b in p.benefits or []:
        if b.get("amount_gb") not in (None, UNLIMITED):
            out.add(float(b["amount_gb"]))
        out |= _numbers(b.get("service", ""))
    return out | _numbers(p.name) | _numbers(p.conditions or "") | _numbers(p.campaign or "")


def _sentences(text: str) -> list[str]:
    """Split on sentence ends and line breaks, keeping Markdown list lines whole."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ*\-])|\n", text)
    return [p for p in parts if p is not None]


def _cited(sentence: str) -> list[str]:
    ids = []
    for m in _MARKER_RE.finditer(sentence):
        if m.group(2):
            a, b = int(m.group(1)[1:]), int(m.group(2)[1:])
            ids += [f"S{i}" for i in range(min(a, b), max(a, b) + 1)]
        else:
            ids.append(m.group(1))
    return ids


def guard(answer: str, packages: list[Package], user_text: str, notes: list[str] | None = None) -> GuardResult:
    by_sid = {f"S{i}": p for i, p in enumerate(packages, start=1)}
    violations: list[str] = []

    # Unknown citation ids -> drop the marker (the claim is checked below like any other).
    def _fix_marker(m: re.Match) -> str:
        ids = _cited(m.group(0))
        if all(i in by_sid for i in ids):
            return m.group(0)
        violations.append(f"unknown citation {m.group(0)}")
        return ""
    text = _MARKER_RE.sub(_fix_marker, answer)
    if _URL_RE.search(text):
        violations.append("url in answer")
        text = _URL_RE.sub("", text)

    user_numbers = _numbers(user_text)
    all_money = set().union(*(_money_facts(p) for p in packages)) if packages else set()
    all_gb = set().union(*(_gb_facts(p) for p in packages)) if packages else set()
    any_unlimited = any(p.internet_gb == UNLIMITED for p in packages)

    kept: list[str] = []
    dropped = 0
    considered = 0
    for sentence in _sentences(text):
        if not sentence.strip():
            kept.append(sentence)
            continue
        considered += 1
        low = _fold(sentence)
        scope = [by_sid[i] for i in _cited(sentence) if i in by_sid]
        money = set().union(*(_money_facts(p) for p in scope)) if scope else all_money
        gbs = set().union(*(_gb_facts(p) for p in scope)) if scope else all_gb
        problems = []
        for m in _TL_RE.finditer(sentence):
            v = float(m.group(1).replace(".", "") + (f".{m.group(2)}" if m.group(2) else ""))
            if not any(abs(v - a) <= 1 for a in money | user_numbers):
                problems.append(f"unsupported price {m.group(0)}")
        for m in _GB_RE.finditer(sentence):
            v = float(m.group(1).replace(",", "."))
            if not any(abs(v - a) < 0.01 for a in gbs | user_numbers):
                problems.append(f"unsupported amount {m.group(0)}")
        unlimited_scope = any(p.internet_gb == UNLIMITED for p in scope) if scope else any_unlimited
        general, restricted = _unlimited_claims(low)
        if general and not unlimited_scope:
            problems.append("unlimited general internet claimed")
        if restricted and not _restricted_claim_supported(
                restricted, low, scope or packages):
            problems.append("unlimited app data not in the source")
        named_ops = {op for op, rx in _OPERATOR_NAME_RE.items() if rx.search(low)}
        if scope and named_ops and not named_ops & {p.operator for p in scope}:
            problems.append("operator does not match the cited source")
        cmp_problem = _comparison_problem(low, scope, packages)
        if cmp_problem:
            problems.append(cmp_problem)
        if _LIFELONG_RE.search(low) and not _NEGATION_RE.search(low):
            problems.append("lifelong/permanent benefit claimed")
        for p in scope or packages:
            for b in p.benefits or []:
                if b.get("offer") != "free_trial":
                    continue
                key = _fold(str(b.get("service", "")).split()[0]) if b.get("service") else ""
                if key and key in low and _FREE_RE.search(low) and "deneme" not in low and not re.search(
                        r"\d+\s*ay", low):
                    problems.append(f"trial presented as included: {b.get('service')}")
        if problems:
            violations += problems
            dropped += 1
            continue
        flagged = [i for i in _cited(sentence) if getattr(by_sid.get(i), "retrieval_status", "match")
                   in ("compromise", "unverified")]
        if flagged and not _HEDGE_RE.search(low):
            violations.append(f"flagged source without caveat {flagged}")
            sentence = sentence.rstrip() + " " + " ".join(_caveat(i, by_sid[i]) for i in dict.fromkeys(flagged))
        kept.append(sentence)

    result = "\n".join(kept).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    too_much = considered and dropped / considered > MAX_DROPPED_SHARE
    nothing_cited = packages and not _MARKER_RE.search(result) and _MARKER_RE.search(answer)
    if too_much or nothing_cited or not result:
        logger.warning("answer replaced by grounded fallback (%d violations)", len(violations))
        return GuardResult(fallback_answer(packages, notes), violations, fallback=True)
    result = _status_header(packages, result)
    return GuardResult(result, violations)


def _caveat(sid: str, p: Package) -> str:
    missing = [label for label, ok in getattr(p, "retrieval_checks", []) if ok is not True]
    if getattr(p, "retrieval_status", "") == "compromise":
        return f"(Not: [{sid}] şu şartını karşılamıyor: {', '.join(missing) or 'bir şartın'}.)"
    return f"(Not: [{sid}] için şu bilgi kaynakta doğrulanamadı: {', '.join(missing) or 'bir şart'}.)"


def _status_header(packages: list[Package], answer: str) -> str:
    """When nothing fully matches, say so first -- deterministically, not by trust."""
    statuses = {getattr(p, "retrieval_status", "match") for p in packages}
    if not packages or "match" in statuses:
        return answer
    head = " ".join(_sentences(answer)[:2])
    if _NO_MATCH_RE.search(_fold(head)):
        return answer
    if statuses == {"unverified"}:
        line = ("**Şartlarının tamamını kaynakta doğrulanmış biçimde karşılayan paket yok;** aşağıdakilerde "
                "bazı bilgiler operatörden teyit edilmeli.")
    else:
        line = "**Şartlarının tamamını karşılayan paket bulamadım;** aşağıdakiler hangi şartı karşılamadığı belirtilen alternatifler."
    return f"{line}\n\n{answer}"


def _short(p: Package) -> str:
    internet = internet_phrase(p.internet_gb, p.social_media_gb, p.bonus_internet_gb, p.bonus_internet_scope)
    if p.price_try is None:
        price = "fiyat kaynakta belirtilmemiş"
    elif p.line_type == "prepaid":
        price = f"{fmt_tl(p.price_try)}, {p.validity_days} gün geçerli"
        calc = price_30d(p)
        if calc is not None:
            price += f" (kıyas için 30 günlük karşılığı yaklaşık {fmt_tl(calc)})"
    else:
        price = f"{fmt_tl(p.price_try)} {period_label(p)}"
        if p.commitment_status == "committed" and p.commitment_months:
            price += f", {p.commitment_months} ay taahhütlü"
            if p.no_commitment_price_try is not None:
                price += f" (taahhütsüz {fmt_tl(p.no_commitment_price_try)})"
    return f"{OPERATOR_TR.get(p.operator, p.operator)} {p.name}: {price}" + (f"; {internet}" if internet else "")


def fallback_answer(packages: list[Package], notes: list[str] | None = None) -> str:
    """A plain, fully grounded answer rendered from the facts alone."""
    if not packages:
        return "Bu soruya uygun bir paket bulunamadı."
    lines = []
    statuses = {getattr(p, "retrieval_status", "match") for p in packages}
    if "match" not in statuses:
        lines.append("Şartlarının tamamını karşılayan paket bulamadım. En yakın seçenekler:")
    for i, p in enumerate(packages[:3], start=1):
        status = getattr(p, "retrieval_status", "match")
        tail = ""
        if status != "match":
            tail = " " + _caveat(f"S{i}", p)
        lead = "**Öneri:** " if i == 1 and status == "match" else "- "
        lines.append(f"{lead}{_short(p)} [S{i}].{tail}")
    lines.append("Ayrıntılar ve resmi sayfa bağlantıları kartlarda yer alıyor.")
    return "\n".join(lines)


_ANY_MONEY_RE = re.compile(r"\d+\s*(?:tl\b|₺|lira)|\[S\d+\]", re.I)


def guard_clarification(answer: str, missing: list[str]) -> str:
    """A clarification turn has no sources, so it may not name prices or sources at
    all (the model liked to invent '150 TL alti' bands); replace it if it does."""
    if not _ANY_MONEY_RE.search(answer) and not _URL_RE.search(answer):
        return answer
    asks = {
        "line_type": "Faturalı mı faturasız mı hat düşünüyorsun?",
        "budget": "Aylık ne kadar ödemeyi düşünüyorsun?",
        "usage": "En çok neyi kullanıyorsun: internet mi, konuşma mı?",
    }
    questions = [asks[m] for m in missing if m in asks] or ["Biraz daha ayrıntı verir misin?"]
    return "Sana uygun tarifeyi bulabilmem için:\n" + "\n".join(f"- {q}" for q in questions)
