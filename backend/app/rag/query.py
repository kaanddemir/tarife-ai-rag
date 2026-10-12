"""Structured tariff query: what the user asked for, parsed deterministically from
Turkish text and merged across turns.

One user message -> ``parse_turn`` -> a ``TariffQuery`` whose ``None`` fields mean
"not said in this message". ``conversation_query`` parses each user turn on its own
(assistant text is never parsed) and folds them oldest-first with ``merge``: the
newest explicit statement of a field wins, so "Bütçem 900 TL oldu" replaces an earlier
700 TL ceiling instead of adding to it. A clear new-search phrase starts from scratch.

The rules are small and per-slot on purpose: every rule is anchored to a unit or a
keyword ("TL", "GB", "dakika", "yaş", "taahhüt", "yenilen"), so a number is only ever
read as the slot its unit names. No LLM call is involved: simple requests stay
deterministic and fast, and an extracted constraint can never be hallucinated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from typing import Optional

from app.models import ChatTurn
from app.rag.history import is_conversational
from app.rag.retrieve import (
    detect_activation_channel,
    detect_audience_segment,
    detect_customer_type,
    detect_price_bounds,
    tr_lower,
)

# Constraints are read from at most this many earlier user turns (bounded, predictable).
USER_TURN_LIMIT = 8

OPERATORS = ("turkcell", "vodafone", "turk_telekom")

OBJECTIVES = ("cheapest", "most_data", "best_value", "longest_validity", "most_benefits")


@dataclass(frozen=True)
class TariffQuery:
    """Hard requirements and the ranking objective. ``None`` = not stated."""

    operators: Optional[frozenset] = None      # allowed operators; None = any
    line_type: Optional[str] = None            # prepaid | postpaid | "any" (explicitly either)
    min_price: Optional[float] = None
    max_price: Optional[float] = None          # TL, on the ongoing (post-promo) price
    monthly_budget: Optional[bool] = None      # "aylık": long prepaid packages compare per 30 days
    min_gb: Optional[float] = None             # general-use GB
    max_gb: Optional[float] = None
    unlimited_internet: Optional[bool] = None  # unlimited GENERAL internet
    unlimited_social: Optional[bool] = None
    apps: Optional[frozenset] = None           # apps whose traffic must not use the general quota
    subscriptions: Optional[frozenset] = None  # services wanted as an included subscription
    no_trials: Optional[bool] = None           # time-limited free trials do not count
    roaming: Optional[bool] = None
    min_minutes: Optional[int] = None
    unlimited_minutes: Optional[bool] = None
    min_sms: Optional[int] = None
    no_commitment: Optional[bool] = None       # True = prohibited; False = explicitly fine
    renewal: Optional[str] = None              # one_time | recurring
    channel: Optional[str] = None              # number_porting | new_line
    customer_type: Optional[str] = None
    age: Optional[int] = None
    audience: Optional[str] = None             # youth | child | family | public_special
    objective: Optional[str] = None
    # Parse-only signals, not carried by merge.
    operators_additive: bool = False           # "Turkcell de olabilir"
    reset: bool = False                        # "yeni bir arama"

    def stated(self) -> dict:
        skip = {"operators_additive", "reset"}
        return {f.name: getattr(self, f.name) for f in fields(self)
                if f.name not in skip and getattr(self, f.name) is not None}


# ---- operators ----

# Word-start anchored aliases (a bare "tt" used to fire inside "hattım"); common
# misspellings are accepted. tr_lower() runs first, so "TÜRK" == "türk".
_OPERATOR_RE = [
    (re.compile(r"\bturkcel+\b|\bturkcel+(?=['’\s]|$)|\bturkcell\w*"), "turkcell"),
    (re.compile(r"\bvodafon\w*"), "vodafone"),
    (re.compile(r"\bt[uü]rk\s?telekom\w*|\btt\b"), "turk_telekom"),
]
_OP_NEGATION_RE = re.compile(r"^['’]?\w*\s*(?:olmas[ıi]n|hari[cç]|istemiyorum|d[ıi][sş][ıi]nda|de[gğ]il|olmaz)")
_OP_ADDITIVE_RE = re.compile(r"^['’]?\w*\s+(?:de|da)\s+(?:olabilir|olur|bakal[ıi]m|bak|ekle)")
_ANY_OPERATOR_RE = re.compile(r"operat[oö]r\w*\s+(?:fark\s?etmez|[oö]nemli\s+de[gğ]il)|hangi operat[oö]r olursa|"
                              r"t[uü]m operat[oö]rler|b[uü]t[uü]n operat[oö]rler")


def _operators(low: str) -> tuple[Optional[frozenset], bool]:
    wanted, unwanted, additive = set(), set(), False
    for pattern, key in _OPERATOR_RE:
        for m in pattern.finditer(low):
            rest = low[m.end():m.end() + 30]
            if _OP_NEGATION_RE.match(rest):
                unwanted.add(key)
            else:
                wanted.add(key)
                additive = additive or bool(_OP_ADDITIVE_RE.match(rest))
    if _ANY_OPERATOR_RE.search(low):
        return frozenset(OPERATORS), False
    if wanted:
        return frozenset(wanted - unwanted), additive
    if unwanted:
        return frozenset(set(OPERATORS) - unwanted), False
    return None, False


# ---- line type ----

_PREPAID_RE = re.compile(r"faturas[ıi]z|haz[ıi]r\s?kart|[oö]n\s?[oö]demeli|prepaid")
_POSTPAID_RE = re.compile(r"fatural[ıi]|faturam|postpaid")
_NEG_AFTER_RE = re.compile(r"^\w*\s*(?:hat\s+|paket\s+|tarife\s+)?(?:istemiyorum|olmas[ıi]n|de[gğ]il|olmaz)")
_ANY_LINE_RE = re.compile(r"fatural[ıi]\s+(?:ya\s+da|veya|mi)\s+faturas[ıi]z\w*\s+(?:fark\s?etmez|olur)|"
                          r"fatura\s+tipi\s+fark\s?etmez|ikisi de olur")


def _line_type(low: str) -> Optional[str]:
    if _ANY_LINE_RE.search(low):
        return "any"
    pre = [(m, bool(_NEG_AFTER_RE.match(low[m.end():]))) for m in _PREPAID_RE.finditer(low)]
    post = [(m, bool(_NEG_AFTER_RE.match(low[m.end():]))) for m in _POSTPAID_RE.finditer(low)
            if not low[m.start():].startswith("faturas")]
    want = {lt for lt, hits in (("prepaid", pre), ("postpaid", post)) for _, neg in hits if not neg}
    refuse = {lt for lt, hits in (("prepaid", pre), ("postpaid", post)) for _, neg in hits if neg}
    if refuse and not want:
        return "prepaid" if refuse == {"postpaid"} else "postpaid" if refuse == {"prepaid"} else None
    want -= refuse
    return want.pop() if len(want) == 1 else None


# ---- money ----

_RANGE_RE = re.compile(
    r"(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:tl|lira|₺)?\s*(?:ile|-|–|ila)\s*(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:tl|lira|₺)"
    r"[\w'’]*\s*(?:aras[ıi]|aral[ıi][gğ][ıi]nda)?")
_BUDGET_WORD_RE = re.compile(r"b[uü]t[cç]e\w*|ay[ıi]rd[ıi]m|harcayabilirim|verebilirim")
_MONEY_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?\s*(?:tl\b|lira|₺)")
_PAST_PRICE_RE = re.compile(r"[oö]d[uü]yordum|[oö]dedim|[oö]d[uü]yorum|eskiden|[sş]u an\w*\s+\d|[sş]imdiki")
_FLEX_RE = re.compile(r"civar|gibi|a[sş]a[gğ][ıi] yukar[ıi]|esnek")
_MONTHLY_RE = re.compile(r"\bayl[ıi]k|\bayda\b|\bher ay\b")


def _num(text: str) -> float:
    return float(text.replace(".", ""))


def _prices(low: str) -> tuple[Optional[float], Optional[float]]:
    m = _RANGE_RE.search(low)
    if m:
        a, b = sorted((_num(m.group(1)), _num(m.group(2))))
        return a, b
    lo, hi = detect_price_bounds(low)
    amounts = _MONEY_RE.findall(low)
    # A budget word, or a single bare amount in a list of needs ("50 GB, 100 TL"), is a
    # ceiling; an amount the user paid in the past ("eskiden 1500 TL ödüyordum") is not.
    if hi is None and lo is None and (_BUDGET_WORD_RE.search(low) or (
            len(amounts) == 1 and not _PAST_PRICE_RE.search(low))):
        money = _MONEY_RE.search(low)
        if money:
            hi = _num(money.group(1))
            if _FLEX_RE.search(low):
                hi = round(hi * 1.1, 2)  # "900 TL civarı": documented 10% headroom
    return lo, hi


# ---- internet ----

_GB_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(gb|mb)\b")
_GB_MIN_BEFORE_RE = re.compile(r"(?:en az|minimum|asgari|en d[uü][sş][uü]k)\s*$")
_GB_MAX_BEFORE_RE = re.compile(r"(?:en fazla|maksimum|azami)\s*$")
_GB_MIN_AFTER_RE = re.compile(r"^['’]?\w*\s*(?:[uü]st[uü]|[uü]zeri|ve [uü]zeri|fazla|ve daha fazla|\+)")
_GB_MAX_AFTER_RE = re.compile(r"^['’]?\w*\s*(?:kadar|alt[ıi]|az\b|ge[cç]mesin)")
# A GB amount that names a restricted quota is not a general-internet requirement.
_GB_RESTRICTED_AFTER_RE = re.compile(r"^['’]?\w*\s*(?:sosyal|whatsapp|bip|instagram|youtube|tiktok|uygulama)")

_UNLIMITED_RE = re.compile(r"s[ıi]n[ıi]rs[ıi]z")
_UNLIMITED_GENERAL_RE = re.compile(r"s[ıi]n[ıi]rs[ıi]z\s+(?:genel\s+)?(?:internet|data|gb)|"
                                   r"(?:internet|data)\w*\s+s[ıi]n[ıi]rs[ıi]z|internet\s+s[ıi]n[ıi]r[ıi]\s+olmas[ıi]n")
_SOCIAL_RE = re.compile(r"sosyal\s+medya")
_UNLIMITED_SOCIAL_RE = re.compile(r"s[ıi]n[ıi]rs[ıi]z\s+sosyal|sosyal\s+medya\w*\s+(?:\w+\s+)?s[ıi]n[ıi]rs[ıi]z")


def _gb(low: str) -> tuple[Optional[float], Optional[float]]:
    lo = hi = None
    for m in _GB_RE.finditer(low):
        value = float(m.group(1).replace(",", "."))
        if m.group(2) == "mb":
            value /= 1000
        before, after = low[max(0, m.start() - 15):m.start()], low[m.end():m.end() + 25]
        if _GB_RESTRICTED_AFTER_RE.match(after):
            continue
        if _GB_MAX_BEFORE_RE.search(before) or _GB_MAX_AFTER_RE.match(after):
            hi = value if hi is None else hi
        else:  # "en az 40 GB", "40 GB üstü", and a bare "40 GB" are all a minimum need
            lo = value if lo is None else lo
    return lo, hi


# ---- apps, subscriptions, benefits ----

_APPS = {
    "netflix": "netflix", "youtube": "youtube", "instagram": "instagram", "insta": "instagram",
    "tiktok": "tiktok", "whatsapp": "whatsapp", "bip": "bip", "spotify": "spotify", "facebook": "facebook",
    "twitter": "x", "amazon prime": "amazon prime", "prime video": "amazon prime", "yaay": "yaay",
    "fizy": "fizy", "lifebox": "lifebox", "tv+": "tv+", "chatgpt": "chatgpt", "muud": "muud",
    "tivibu": "tivibu", "disney": "disney", "exxen": "exxen", "blutv": "blutv",
}
_APP_RE = re.compile(r"\b(" + "|".join(re.escape(a) for a in sorted(_APPS, key=len, reverse=True)) + r")")
# Cues within the same clause: data that does not use the general quota...
_DATA_CUE_RE = re.compile(r"kota\w*\s*(?:dan|den)?\s*(?:yemesin|d[uü][sş]mesin|gitmesin|harcamas[ıi]n|say[ıi]lmas[ıi]n)|"
                          r"internet\w*\s+(?:yemesin|harcamas[ıi]n|gitmesin|d[uü][sş]mesin)|kotas[ıi]z|"
                          r"s[ıi]n[ıi]rs[ıi]z|bedava internet|[uü]cretsiz internet|ek internet|internet\w* dahil|"
                          r"\bdata\b|\bgb\b")
# ...versus a membership of the service itself.
_SUB_CUE_RE = re.compile(r"[uü]yeli[gğ]\w*|abone\w*|premium|hediye|[uü]cretsiz|bedava|dahil")
_NO_TRIAL_RE = re.compile(r"deneme\w*\s+(?:[uü]yeli[gğ]\w*\s+|s[uü]re\w*\s+)?(?:istemiyorum|olmas[ıi]n|istemem|"
                          r"say[ıi]lmas[ıi]n|de[gğ]il)|kal[ıi]c[ıi]\s+(?:[uü]yelik|abonelik)")
_ROAMING_RE = re.compile(r"yurt\s?d[ıi][sş][ıi]|roaming|avrupa|yurtd[ıi][sş][ıi]")


def _clause(low: str, start: int, end: int) -> str:
    left = max(low.rfind(c, 0, start) for c in ",.;!?") + 1
    rights = [i for i in (low.find(c, end) for c in ",.;!?") if i != -1]
    return low[left:min(rights) if rights else len(low)]


def _apps_and_subscriptions(low: str) -> tuple[Optional[frozenset], Optional[frozenset]]:
    apps, subs = set(), set()
    low = low.replace("ı", "i")  # tr_lower turns "Instagram" into "ınstagram"
    for m in _APP_RE.finditer(low):
        app = _APPS[m.group(1)]
        clause = _clause(low, m.start(), m.end())
        if _DATA_CUE_RE.search(clause):
            apps.add(app)
        elif _SUB_CUE_RE.search(clause):
            subs.add(app)
        # A bare mention ("Netflix izliyorum") is a soft preference: semantic only.
    return (frozenset(apps) if apps else None), (frozenset(subs) if subs else None)


# ---- calls, SMS ----

_MINUTES_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:dk\b|dakika)")
_SMS_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:sms|mesaj)")
_UNLIMITED_CALLS_RE = re.compile(r"s[ıi]n[ıi]rs[ıi]z\s+(?:konu[sş]ma|dakika|arama)|(?:konu[sş]ma|dakika)\w*\s+s[ıi]n[ıi]rs[ıi]z")

# ---- commitment, renewal ----

_NO_COMMIT_RE = re.compile(r"taahh[uü]ts[uü]z|taahh[uü]t\w*\s+(?:yok|olmadan|olmayan)|taahh[uü]\w*\s+(?:\w+\s+)?(?:istemiyorum|olmas[ıi]n|istemem|"
                           r"girmek istemiyorum|vermek istemiyorum)|s[oö]zle[sş]me\w*\s+istemiyorum")
_COMMIT_OK_RE = re.compile(r"taahh[uü]\w*\s+(?:olabilir|olur|sorun de[gğ]il|fark etmez|problem de[gğ]il)|"
                           r"taahh[uü]tl[uü]\s+(?:da\s+|de\s+)?olur")
_ONE_TIME_RE = re.compile(r"yenilenme(?:sin|yen|mesin)|yenilenmeyecek|tek\s?seferlik|"
                          r"otomatik\s+yenilen\w*\s+(?:istemiyorum|olmas[ıi]n)|bir\s+kerelik")
_RECURRING_RE = re.compile(r"(?:otomatik|her ay|d[uü]zenli)\s+(?:olarak\s+)?yenilen(?:sin|en|ebilen)")

# ---- objective ----

_OBJECTIVE_RE = [
    ("most_data", re.compile(r"en\s+(?:[cç]ok|fazla|y[uü]ksek|bol)\s+(?:internet|gb|data)|internet\w*\s+en\s+(?:[cç]ok|fazla)")),
    ("best_value", re.compile(r"fiyat\s*/?\s*performans|\bf/?p\b|en\s+avantajl[ıi]|gb\s+ba[sş][ıi]na|"
                              r"paras[ıi]n[ıi]n\s+kar[sş][ıi]l[ıi][gğ][ıi]")),
    ("longest_validity", re.compile(r"en\s+uzun\s+(?:s[uü]reli|ge[cç]erli|s[uü]re)")),
    ("most_benefits", re.compile(r"(?:en\s+[cç]ok|fazla)\s+(?:avantaj|hediye|ayr[ıi]cal[ıi]k)|avantaj[ıi]\s+fazla")),
    ("cheapest", re.compile(r"en\s+(?:ucuz|uygun\s+fiyat|hesapl[ıi]|d[uü][sş][uü]k\s+fiyat|ekonomik)|ucuzu\w*|"
                            r"en\s+az\s+(?:para|[uü]cret)")),
]

_RESET_RE = re.compile(r"yeni\s+bir\s+(?:arama|soru|konu)|ba[sş]tan\s+(?:ba[sş]la|al)|s[ıi]f[ıi]rdan|"
                       r"her\s+[sş]eyi\s+unut|[oö]ncekileri\s+unut|ba[sş]ka\s+bir\s+(?:paket|konu)\s+(?:i[cç]in|soraca)")


# ---- spoken numbers and slang ----

_DIGITS = {"bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4, "beş": 5, "bes": 5, "altı": 6, "alti": 6,
           "yedi": 7, "sekiz": 8, "dokuz": 9}
_TENS = {"on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "kirk": 40, "elli": 50, "altmış": 60, "altmis": 60,
         "yetmiş": 70, "yetmis": 70, "seksen": 80, "doksan": 90}
_WORD = "|".join(sorted([*_DIGITS, *_TENS, "yüz", "yuz", "bin"], key=len, reverse=True))
# A number in words counts only when a unit follows it directly, so "bir paket" or
# "300 TL altı" are never read as numbers.
_SPOKEN_RE = re.compile(rf"\b((?:(?:{_WORD})\s*)+)(?=(?:tl\b|lira|₺|gb\b|giga|gig\b|dakika|dk\b|sms|ya[sş]))")


def _spoken_value(words: str) -> Optional[int]:
    total = current = 0
    for w in re.findall(_WORD, words):
        if w == "bin":
            total += (current or 1) * 1000
            current = 0
        elif w in ("yüz", "yuz"):
            current = (current or 1) * 100
        else:
            current += _DIGITS.get(w) or _TENS[w]
    return total + current or None


def normalize_numbers(low: str) -> str:
    """'iki yüz elli lira' -> '250 lira', 'yüz gigadan' -> '100 gb'dan', 'sınırsız net' -> 'sınırsız internet'."""
    def spoken(m: re.Match) -> str:
        value = _spoken_value(m.group(1))
        return f"{value} " if value else m.group(0)
    low = _SPOKEN_RE.sub(spoken, low)
    low = re.sub(r"(\d)\s*(?:gigabayt|gigabyte|giga|gig)(?:\b|(?=[a-zçğıöşü]))['’]?", r"\1 gb'", low)
    return re.sub(r"(s[ıi]n[ıi]rs[ıi]z)\s+net\b", r"\1 internet", low)


# "20 yaşındayım", "20 yaşında", "20 yaş": the inflected forms the shared detector misses.
_AGE_RE = re.compile(r"\b(\d{1,2})\s*ya[sş]\w*")


def _age(low: str) -> Optional[int]:
    m = _AGE_RE.search(low)
    return int(m.group(1)) if m else None


def _int(text: str) -> int:
    return int(text.replace(".", ""))


def parse_turn(text: str) -> TariffQuery:
    """Parse ONE user message. Unmentioned slots stay None."""
    low = normalize_numbers(tr_lower(text))
    text = low  # detectors below read the normalized text too
    operators, additive = _operators(low)
    min_price, max_price = _prices(low)
    min_gb, max_gb = _gb(low)
    apps, subs = _apps_and_subscriptions(low)
    unlimited_social = True if _UNLIMITED_SOCIAL_RE.search(low) else None
    unlimited_internet = None
    if _UNLIMITED_GENERAL_RE.search(low) and not unlimited_social:
        unlimited_internet = True
    minutes = _MINUTES_RE.search(low)
    sms = _SMS_RE.search(low)
    objective = next((name for name, rx in _OBJECTIVE_RE if rx.search(low)), None)
    if objective == "cheapest" and min_price is not None and re.search(r"en\s+az\s+\d", low):
        objective = None  # "en az 1000 TL" is a minimum price, not "the cheapest"
    channel = detect_activation_channel(text)
    if channel is None and re.search(r"(?:hatt[ıi]m[ıi]|numaram[ıi])\s+(?:\S+\s+){0,2}ge[cç]ir", low):
        channel = "number_porting"
    audience = detect_audience_segment(text)
    if audience is None and re.search(r"parlamenter|milletvekili", low):
        audience = "public_special"
    customer_type = detect_customer_type(text)
    return TariffQuery(
        operators=operators,
        line_type=_line_type(low),
        min_price=min_price,
        max_price=max_price,
        monthly_budget=True if (max_price is not None and _MONTHLY_RE.search(low)) else None,
        min_gb=min_gb,
        max_gb=max_gb,
        unlimited_internet=unlimited_internet,
        unlimited_social=unlimited_social,
        apps=apps,
        subscriptions=subs,
        no_trials=True if _NO_TRIAL_RE.search(low) else None,
        roaming=True if _ROAMING_RE.search(low) else None,
        min_minutes=_int(minutes.group(1)) if minutes else None,
        unlimited_minutes=True if _UNLIMITED_CALLS_RE.search(low) else None,
        min_sms=_int(sms.group(1)) if sms else None,
        no_commitment=True if _NO_COMMIT_RE.search(low) else (False if _COMMIT_OK_RE.search(low) else None),
        renewal="one_time" if _ONE_TIME_RE.search(low) else ("recurring" if _RECURRING_RE.search(low) else None),
        channel=channel,
        # "new" is the whole catalog; only "existing" changes retrieval.
        customer_type=customer_type if customer_type == "existing" else None,
        age=_age(low),
        audience=audience,
        objective=objective,
        operators_additive=additive,
        reset=bool(_RESET_RE.search(low)),
    )


def merge(previous: TariffQuery, new: TariffQuery) -> TariffQuery:
    """Newest explicit value wins; "X de olabilir" widens the operator set."""
    if new.reset:
        return replace(new, reset=False, operators_additive=False)
    updates = new.stated()
    if new.operators is not None and new.operators_additive and previous.operators is not None:
        updates["operators"] = previous.operators | new.operators
    # A new price statement replaces the whole budget (a fresh ceiling drops an old floor).
    if "max_price" in updates or "min_price" in updates:
        updates.setdefault("min_price", None)
        updates.setdefault("max_price", None)
        updates.setdefault("monthly_budget", None)
    if "min_gb" in updates or "max_gb" in updates:
        updates.setdefault("min_gb", None)
        updates.setdefault("max_gb", None)
    return replace(previous, **updates)


def normalized(query: TariffQuery) -> TariffQuery:
    """Collapse "explicitly any" markers into no constraint."""
    ops = query.operators
    return replace(
        query,
        operators=None if ops is not None and set(ops) == set(OPERATORS) else ops,
        line_type=None if query.line_type == "any" else query.line_type,
        operators_additive=False,
        reset=False,
    )


def conversation_query(question: str, history: list[ChatTurn] | None = None) -> TariffQuery:
    """Fold the recent USER turns and the question into one query. Assistant replies
    (including provider-error bubbles, which recent_history drops) are never parsed."""
    query = TariffQuery()
    user_turns = [t for t in (history or []) if t.role == "user" and is_conversational(t)]
    for turn in user_turns[-USER_TURN_LIMIT:]:
        query = merge(query, parse_turn(turn.content))
    return normalized(merge(query, parse_turn(question)))
