"""Turkish retrieval regression cases.

Each case is a conversation (user turns, oldest first) plus ground truth written
against the catalog FACTS, not against the retrieval code:

* ``must``      -- predicate every confirmed result has to satisfy (hard constraints).
* ``objective`` -- for deterministic objectives, the record(s) that must rank first are
                   computed by brute force over the catalog records that satisfy ``must``.
* ``empty``     -- no catalog record satisfies ``must``: a correct engine returns no
                   confirmed match (labelled compromises are allowed).
* ``slots``     -- expected values of the parsed query (checked by the offline tests).

Eligibility ground truth: unless the user states an age or a special audience, only
general-audience offers are eligible; a province-restricted offer is never a confirmed
match (the user's province is unknown); parliamentarian offers need "parlamenter".

Shared by the query-parsing and PostgreSQL retrieval tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from app.ingest.normalize import general_internet_gb

INF = float("inf")


# ---- facts over catalog record dicts (independent of app.rag) ----

def gen(r) -> float:
    g = general_internet_gb(r.get("internet_gb"), r.get("bonus_internet_gb"), r.get("bonus_internet_scope"))
    return INF if g == -1 else float(g or 0)


def ongoing(r, no_commit: bool = False) -> Optional[float]:
    if no_commit and r["commitment_status"] == "committed":
        price = r.get("no_commitment_price_try")
    else:
        price = r.get("price_after_promo_try") or r["price_try"]
    return None if price is None else float(price)


def p30(r, no_commit: bool = False) -> float:
    """30-day comparison price of the ongoing price."""
    price = ongoing(r, no_commit)
    if price is None:
        return INF
    if r["line_type"] == "prepaid" and r.get("validity_days"):
        return price * 30 / r["validity_days"]
    return price


def days(r) -> int:
    return r.get("validity_days") or 30


def apps_free(r, app: str) -> bool:
    app = app.lower()
    social = [a.lower() for a in (r.get("social_media_apps") or [])]
    if r.get("social_media_gb") and app in social:
        return True
    return any(b["category"] == "app_data" and b["offer"] == "included"
               and app in [a.lower() for a in (b.get("apps") or [])] for b in r["benefits"])


def subscription(r, name: str, trials_ok: bool = True) -> bool:
    offers = {"included", "free_trial"} if trials_ok else {"included"}
    return any(b["category"] == "subscription" and b["offer"] in offers and name.lower() in b["service"].lower()
               for b in r["benefits"])


def roaming(r) -> bool:
    return any(b["category"] == "roaming" and b["offer"] == "included" for b in r["benefits"])


def no_commit_ok(r) -> bool:
    return r["commitment_status"] == "no_commitment" or r.get("no_commitment_price_try") is not None


def channel(r, ch: str) -> bool:
    return ch in (r.get("activation_channels") or [])


def safe(r) -> bool:
    """Eligible for a user who stated no age or special audience."""
    tags = r.get("eligibility_tags") or []
    return r["audience_segment"] == "general" and "parliamentarian" not in tags and "province_restricted" not in tags


def age_ok(age: int, segments=("general", "youth", "child")):
    def check(r):
        tags = r.get("eligibility_tags") or []
        if "parliamentarian" in tags or "province_restricted" in tags or r["audience_segment"] not in segments:
            return False
        lo, hi = r.get("age_min"), r.get("age_max")
        if r["audience_segment"] == "general":
            return True
        return (lo is None or lo <= age) and (hi is None or hi >= age)
    return check


def aud(*segments):
    def check(r):
        tags = r.get("eligibility_tags") or []
        if "province_restricted" in tags:
            return False
        if "parliamentarian" in tags and "public_special" not in segments:
            return False
        return r["audience_segment"] in segments
    return check


def op(*ops):
    return lambda r: r["operator"] in ops


def line(lt):
    return lambda r: r["line_type"] == lt


def all_(*preds):
    return lambda r: all(p(r) for p in preds)


# Objective sort keys (lower is better), used to compute the expected top-1 set.
OBJECTIVES: dict[str, Callable] = {
    "cheapest": lambda r, nc=False: p30(r, nc),
    "most_data": lambda r, nc=False: -gen(r),
    "longest_validity": lambda r, nc=False: -days(r),
}


@dataclass
class Case:
    id: str
    turns: list[str]
    must: Callable
    category: str
    objective: Optional[str] = None
    no_commit: bool = False  # the objective uses the no-commitment price
    empty: bool = False
    slots: dict = field(default_factory=dict)


def _c(id, turns, must, category, **kw) -> Case:
    return Case(id=id, turns=[turns] if isinstance(turns, str) else turns, must=must, category=category, **kw)


PRE, POST = line("prepaid"), line("postpaid")

CASES: list[Case] = [
    # -- numeric: price ceilings, ranges, minimum general data
    _c("n01", "faturasız 200 TL altı paket", all_(PRE, safe, lambda r: ongoing(r) <= 200), "numeric",
       slots={"line_type": "prepaid", "max_price": 200.0}),
    _c("n02", "Vodafone faturalı en az 1000 TL", all_(op("vodafone"), POST, safe, lambda r: r["price_try"] >= 1000),
       "numeric", slots={"operators": {"vodafone"}, "min_price": 1000.0}),
    _c("n03", "Turkcell faturalı en az 40 GB", all_(op("turkcell"), POST, safe, lambda r: gen(r) >= 40), "numeric",
       slots={"operators": {"turkcell"}, "min_gb": 40.0}),
    _c("n04", "Türk Telekom faturasız 50 GB üstü", all_(op("turk_telekom"), PRE, safe, lambda r: gen(r) >= 50),
       "numeric", slots={"operators": {"turk_telekom"}, "min_gb": 50.0}),
    _c("n05", "faturalı 500 ile 800 TL arası", all_(POST, safe, lambda r: 500 <= ongoing(r) <= 800), "numeric",
       slots={"min_price": 500.0, "max_price": 800.0}),
    _c("n06", "500-800 TL arası faturasız paket", all_(PRE, safe, lambda r: 500 <= ongoing(r) <= 800), "numeric",
       slots={"min_price": 500.0, "max_price": 800.0}),
    _c("n07", "Aylık 600 lirayı geçmesin, faturalı olsun", all_(POST, safe, lambda r: ongoing(r) <= 600), "numeric",
       slots={"max_price": 600.0, "line_type": "postpaid"}),
    _c("n08", "en az 40 GB faturalı", all_(POST, safe, lambda r: gen(r) >= 40), "numeric", slots={"min_gb": 40.0}),
    _c("n09", "40 GB üstü faturasız", all_(PRE, safe, lambda r: gen(r) >= 40), "numeric", slots={"min_gb": 40.0}),
    _c("n10", "faturalı 50 GB", all_(POST, safe, lambda r: gen(r) >= 50), "numeric", slots={"min_gb": 50.0}),
    _c("n11", "faturalı en az 30 GB genel internet 600 TL altı",
       all_(POST, safe, lambda r: gen(r) >= 30, lambda r: ongoing(r) <= 600), "numeric",
       slots={"min_gb": 30.0, "max_price": 600.0}),
    _c("n12", "faturasiz 300tl alti", all_(PRE, safe, lambda r: ongoing(r) <= 300), "spelling",
       slots={"max_price": 300.0, "line_type": "prepaid"}),
    _c("n13", "faturalı 1.000 TL altı sınırsız sosyal medya",
       all_(POST, safe, lambda r: ongoing(r) <= 1000, lambda r: r.get("social_media_gb") == -1), "numeric",
       slots={"max_price": 1000.0, "unlimited_social": True}),
    _c("n14", "en az 1000 dakika faturasız 20 GB",
       all_(PRE, safe, lambda r: gen(r) >= 20, lambda r: r["voice_minutes"] == -1 or r["voice_minutes"] >= 1000),
       "numeric", slots={"min_minutes": 1000, "min_gb": 20.0}),
    _c("n15", "sınırsız konuşma olan faturalı tarife", all_(POST, safe, lambda r: r["voice_minutes"] == -1), "numeric",
       slots={"unlimited_minutes": True}),
    _c("n16", "faturasız 5 GB'a kadar paket", all_(PRE, safe, lambda r: gen(r) <= 5), "numeric",
       slots={"max_gb": 5.0}),
    # -- general vs restricted internet
    _c("g01", "Turkcell faturalı en az 40 GB genel internet", all_(op("turkcell"), POST, safe, lambda r: gen(r) >= 40),
       "general_vs_restricted", slots={"min_gb": 40.0}),
    _c("g02", "faturalı 30 GB internet, sosyal medya kotası sayılmasın", all_(POST, safe, lambda r: gen(r) >= 30),
       "general_vs_restricted", slots={"min_gb": 30.0}),
    _c("g03", "faturasız 40 GB", all_(PRE, safe, lambda r: gen(r) >= 40), "general_vs_restricted"),
    _c("g04", "faturalı sınırsız internet", all_(POST, safe, lambda r: gen(r) == INF), "general_vs_restricted",
       slots={"unlimited_internet": True}),
    _c("g05", "faturasız sınırsız internet", all_(PRE, safe, lambda r: gen(r) == INF), "no_match", empty=True,
       slots={"unlimited_internet": True}),
    _c("g06", "sosyal medya sınırsız olsun, faturalı", all_(POST, safe, lambda r: r.get("social_media_gb") == -1),
       "general_vs_restricted", slots={"unlimited_social": True, "unlimited_internet": None}),
    _c("g07", "sınırsız sosyal medya ve en az 50 GB genel internet, faturalı",
       all_(POST, safe, lambda r: r.get("social_media_gb") == -1, lambda r: gen(r) >= 50), "general_vs_restricted",
       slots={"unlimited_social": True, "min_gb": 50.0}),
    _c("g08", "Vodafone faturalı 80 GB", all_(op("vodafone"), POST, safe, lambda r: gen(r) >= 80),
       "general_vs_restricted", slots={"min_gb": 80.0}),
    _c("g09", "faturalı en az 60 GB, sosyal medya kotası değil genel olsun",
       all_(POST, safe, lambda r: gen(r) >= 60), "general_vs_restricted"),
    # -- app data vs subscriptions vs trials
    _c("b01", "Instagram kotamdan yemesin, faturalı", all_(POST, safe, lambda r: apps_free(r, "instagram")), "benefits",
       slots={"apps": {"instagram"}}),
    _c("b02", "WhatsApp sınırsız olsun faturasız", all_(PRE, safe, lambda r: apps_free(r, "whatsapp")), "benefits",
       slots={"apps": {"whatsapp"}}),
    _c("b03", "Netflix internetimi yemesin, faturalı", all_(POST, safe, lambda r: apps_free(r, "netflix")), "benefits",
       slots={"apps": {"netflix"}, "subscriptions": set()}),
    _c("b04", "Netflix üyeliği hediye olsun, faturalı", all_(POST, safe, lambda r: subscription(r, "netflix")),
       "benefits", slots={"subscriptions": {"netflix"}, "apps": set()}),
    _c("b05", "Netflix üyeliği hediye olsun ama deneme üyeliği istemiyorum, faturalı",
       all_(POST, safe, lambda r: subscription(r, "netflix", trials_ok=False)), "no_match", empty=True,
       slots={"subscriptions": {"netflix"}, "no_trials": True}),
    _c("b06", "Spotify üyeliği hediye olan faturalı tarife", all_(POST, safe, lambda r: subscription(r, "spotify")),
       "no_match", empty=True, slots={"subscriptions": {"spotify"}}),
    _c("b07", "YouTube Premium hediye faturalı", all_(POST, safe, lambda r: subscription(r, "youtube")), "benefits",
       slots={"subscriptions": {"youtube"}}),
    _c("b08", "yurt dışında da kullanabileceğim faturalı tarife", all_(POST, safe, roaming), "benefits",
       slots={"roaming": True}),
    _c("b09", "BiP sınırsız faturasız paket", all_(PRE, safe, lambda r: apps_free(r, "bip")), "benefits",
       slots={"apps": {"bip"}}),
    _c("b10", "TikTok kotadan düşmesin faturalı", all_(POST, safe, lambda r: apps_free(r, "tiktok")), "benefits",
       slots={"apps": {"tiktok"}}),
    _c("b11", "Netflix aboneliği ücretsiz olan Türk Telekom faturalı",
       all_(op("turk_telekom"), POST, safe, lambda r: subscription(r, "netflix")), "benefits",
       slots={"subscriptions": {"netflix"}}),
    # -- commitment
    _c("c01", "taahhüt istemiyorum, faturalı 30 GB", all_(POST, safe, no_commit_ok, lambda r: gen(r) >= 30),
       "commitment", slots={"no_commitment": True}),
    _c("c02", "taahhütsüz faturalı 800 TL altı 20 GB",
       all_(POST, safe, no_commit_ok, lambda r: gen(r) >= 20, lambda r: ongoing(r, True) <= 800), "commitment",
       slots={"no_commitment": True, "max_price": 800.0}),
    _c("c03", "taahhütsüz faturalı en ucuz", all_(POST, safe, no_commit_ok), "commitment", objective="cheapest",
       no_commit=True, slots={"no_commitment": True, "objective": "cheapest"}),
    _c("c04", "50 GB, 100 TL, taahhütsüz faturalı",
       all_(POST, safe, no_commit_ok, lambda r: gen(r) >= 50, lambda r: ongoing(r, True) <= 100), "no_match",
       empty=True),
    _c("c05", "taahhüde girmek istemiyorum, faturalı Vodafone 1500 TL altı",
       all_(op("vodafone"), POST, safe, no_commit_ok, lambda r: ongoing(r, True) <= 1500), "commitment",
       slots={"no_commitment": True}),
    # -- renewal
    _c("r01", "Otomatik yenilenmesin, faturasız paket", all_(PRE, safe, lambda r: r["renewal_type"] == "one_time"),
       "renewal", slots={"renewal": "one_time"}),
    _c("r02", "otomatik yenilenmeyen faturasız 50 GB",
       all_(PRE, safe, lambda r: r["renewal_type"] == "one_time", lambda r: gen(r) >= 50), "renewal",
       slots={"renewal": "one_time", "min_gb": 50.0}),
    _c("r03", "tek seferlik faturasız paket, en ucuzu",
       all_(PRE, safe, lambda r: r["renewal_type"] == "one_time"), "renewal", objective="cheapest",
       slots={"renewal": "one_time", "objective": "cheapest"}),
    _c("r04", "her ay otomatik yenilensin, faturasız", all_(PRE, safe, lambda r: r["renewal_type"] == "recurring"),
       "renewal", slots={"renewal": "recurring"}),
    _c("r05", "faturasız otomatik yenilenmesin sınırsız internet",
       all_(PRE, safe, lambda r: r["renewal_type"] == "one_time", lambda r: gen(r) == INF), "no_match", empty=True),
    # -- channel
    _c("ch01", "Numaramı taşıyacağım, faturasız paket", all_(PRE, safe, lambda r: channel(r, "number_porting")),
       "channel", slots={"channel": "number_porting"}),
    _c("ch02", "Numaramı taşırsam faturasız ne var?", all_(PRE, safe, lambda r: channel(r, "number_porting")),
       "channel", slots={"channel": "number_porting"}),
    _c("ch03", "Hattımı Vodafone'a geçirmek istiyorum, faturasız",
       all_(op("vodafone"), PRE, safe, lambda r: channel(r, "number_porting")), "channel",
       slots={"channel": "number_porting", "operators": {"vodafone"}}),
    _c("ch04", "Yeni hat alacağım, faturasız en ucuz", all_(PRE, safe, lambda r: channel(r, "new_line")), "channel",
       objective="cheapest", slots={"channel": "new_line", "objective": "cheapest"}),
    _c("ch05", "numara taşıma ile faturasız 50 GB",
       all_(PRE, safe, lambda r: channel(r, "number_porting"), lambda r: gen(r) >= 50), "channel"),
    _c("ch06", "numaramı taşıyacağım, Vodafone faturasız 100 GB",
       all_(op("vodafone"), PRE, safe, lambda r: channel(r, "number_porting"), lambda r: gen(r) >= 100), "no_match",
       empty=True),
    # -- eligibility and age
    _c("e01", "20 yaşındayım, faturalı 40 GB", all_(POST, age_ok(20), lambda r: gen(r) >= 40), "eligibility",
       slots={"age": 20}),
    _c("e02", "öğrenciyim, faturalı en ucuz", all_(POST, aud("general", "youth")), "eligibility",
       objective="cheapest", slots={"audience": "youth"}),
    _c("e03", "14 yaşındaki çocuğuma faturalı hat", all_(POST, aud("child"), age_ok(14, ("child",))), "eligibility",
       slots={"audience": "child", "age": 14}),
    _c("e04", "30 yaşındayım, faturalı en ucuz", all_(POST, age_ok(30)), "eligibility", objective="cheapest",
       slots={"age": 30}),
    _c("e05", "26 yaşındayım, faturalı genç tarifesi", all_(POST, age_ok(26)), "eligibility", slots={"age": 26}),
    _c("e06", "faturalı en ucuz tarife", all_(POST, safe), "eligibility", objective="cheapest",
       slots={"objective": "cheapest", "age": None}),
    _c("e07", "faturasız en ucuz paket", all_(PRE, safe), "eligibility", objective="cheapest"),
    _c("e08", "parlamenterim, faturalı 20 GB", all_(POST, aud("general", "public_special"), lambda r: gen(r) >= 20),
       "eligibility", slots={"audience": "public_special"}),
    _c("e09", "25 yaşındayım, en ucuz faturalı paket", all_(POST, age_ok(25)), "eligibility", objective="cheapest",
       slots={"age": 25, "objective": "cheapest"}),
    _c("e10", "faturalı 100 TL altı", all_(POST, safe, lambda r: ongoing(r) <= 100), "no_match", empty=True),
    _c("e11", "ailece kullanacağız, faturalı 50 GB", all_(POST, aud("general", "family"), lambda r: gen(r) >= 50),
       "eligibility", slots={"audience": "family"}),
    # -- ranking objectives
    _c("o01", "en çok internet veren faturasız paket hangisi?", all_(PRE, safe), "objective", objective="most_data",
       slots={"objective": "most_data"}),
    _c("o02", "En çok internet veren faturalı hangisi?", all_(POST, safe), "objective", objective="most_data"),
    _c("o03", "Vodafone faturalı en çok GB veren", all_(op("vodafone"), POST, safe), "objective",
       objective="most_data"),
    _c("o04", "en uzun süreli faturasız paket", all_(PRE, safe), "objective", objective="longest_validity",
       slots={"objective": "longest_validity"}),
    _c("o05", "Turkcell faturasız en ucuz", all_(op("turkcell"), PRE, safe), "objective", objective="cheapest"),
    _c("o06", "Türk Telekom faturalı en ucuz 20 GB", all_(op("turk_telekom"), POST, safe, lambda r: gen(r) >= 20),
       "objective", objective="cheapest"),
    _c("o07", "En ucuzunu göster, faturalı 10 GB", all_(POST, safe, lambda r: gen(r) >= 10), "objective",
       objective="cheapest", slots={"objective": "cheapest", "min_gb": 10.0}),
    _c("o08", "fiyat performans olarak en iyi faturalı 50 GB üstü", all_(POST, safe, lambda r: gen(r) >= 50),
       "objective", slots={"objective": "best_value", "min_gb": 50.0}),
    _c("o09", "en hesaplı faturasız 30 GB", all_(PRE, safe, lambda r: gen(r) >= 30), "objective",
       objective="cheapest"),
    # -- spelling / operator false positives / negation
    _c("s01", "turkcel faturali 20gb", all_(op("turkcell"), POST, safe, lambda r: gen(r) >= 20), "spelling",
       slots={"operators": {"turkcell"}, "min_gb": 20.0}),
    _c("s02", "vodafon faturasiz paket", all_(op("vodafone"), PRE, safe), "spelling",
       slots={"operators": {"vodafone"}}),
    _c("s03", "TÜRK TELEKOM FATURALI 30 GB", all_(op("turk_telekom"), POST, safe, lambda r: gen(r) >= 30), "spelling",
       slots={"operators": {"turk_telekom"}}),
    _c("s04", "türktelekom faturasız", all_(op("turk_telekom"), PRE, safe), "spelling"),
    _c("s05", "hattım için faturalı 20 GB", all_(POST, safe, lambda r: gen(r) >= 20), "spelling",
       slots={"operators": None}),
    _c("s06", "Turkcell olmasın, faturalı 30 GB", all_(op("vodafone", "turk_telekom"), POST, safe,
                                                       lambda r: gen(r) >= 30), "negation",
       slots={"operators": {"vodafone", "turk_telekom"}}),
    _c("s07", "Vodafone hariç faturasız en ucuz", all_(op("turkcell", "turk_telekom"), PRE, safe), "negation",
       objective="cheapest"),
    _c("s08", "Faturalı istemiyorum, 30 GB", all_(PRE, safe, lambda r: gen(r) >= 30), "negation",
       slots={"line_type": "prepaid"}),
    _c("s09", "faturasız olmasın, 30 GB", all_(POST, safe, lambda r: gen(r) >= 30), "negation",
       slots={"line_type": "postpaid"}),
    _c("s10", "Eskiden 1500 TL ödüyordum, şimdi faturalı 30 GB arıyorum", all_(POST, safe, lambda r: gen(r) >= 30),
       "negation", slots={"max_price": None, "min_gb": 30.0}),
    # -- multi-turn
    _c("m01", ["Vodafone, en az 30 GB, 700 TL altı.", "Faturasız olsun."],
       all_(op("vodafone"), PRE, safe, lambda r: gen(r) >= 30, lambda r: ongoing(r) <= 700), "multi_turn",
       slots={"operators": {"vodafone"}, "line_type": "prepaid", "min_gb": 30.0, "max_price": 700.0}),
    _c("m02", ["Vodafone, en az 30 GB, 700 TL altı.", "Faturasız olsun.", "Aslında Turkcell de olabilir."],
       all_(op("vodafone", "turkcell"), PRE, safe, lambda r: gen(r) >= 30, lambda r: ongoing(r) <= 700),
       "multi_turn", slots={"operators": {"vodafone", "turkcell"}, "max_price": 700.0}),
    _c("m03", ["Vodafone, en az 30 GB, 700 TL altı.", "Faturasız olsun.", "Aslında Turkcell de olabilir.",
               "Bütçem 900 TL oldu."],
       all_(op("vodafone", "turkcell"), PRE, safe, lambda r: gen(r) >= 30, lambda r: ongoing(r) <= 900),
       "multi_turn", slots={"max_price": 900.0, "min_gb": 30.0}),
    _c("m04", ["Vodafone, en az 30 GB, 700 TL altı.", "Faturasız olsun.", "Aslında Turkcell de olabilir.",
               "Bütçem 900 TL oldu.", "Taahhüt istemiyorum.", "Peki en ucuzu?"],
       all_(op("vodafone", "turkcell"), PRE, safe, lambda r: gen(r) >= 30, lambda r: ongoing(r) <= 900),
       "multi_turn", objective="cheapest",
       slots={"objective": "cheapest", "max_price": 900.0, "no_commitment": True, "line_type": "prepaid"}),
    _c("m05", ["faturalı 40 GB Turkcell", "Bütçem 900 TL oldu"],
       all_(op("turkcell"), POST, safe, lambda r: gen(r) >= 40, lambda r: ongoing(r) <= 900), "multi_turn",
       slots={"max_price": 900.0}),
    _c("m06", ["faturalı Vodafone 30 GB", "Taahhüt istemiyorum"],
       all_(op("vodafone"), POST, safe, no_commit_ok, lambda r: gen(r) >= 30), "multi_turn",
       slots={"no_commitment": True, "operators": {"vodafone"}}),
    _c("m07", ["faturalı 30 GB 600 TL altı", "aslında faturasız olsun"],
       all_(PRE, safe, lambda r: gen(r) >= 30, lambda r: ongoing(r) <= 600), "multi_turn",
       slots={"line_type": "prepaid", "max_price": 600.0}),
    _c("m08", ["Turkcell faturalı 20 GB", "Vodafone'a bakalım"],
       all_(op("vodafone"), POST, safe, lambda r: gen(r) >= 20), "multi_turn", slots={"operators": {"vodafone"}}),
    _c("m09", ["faturasız en ucuz", "en çok internet olan hangisi?"], all_(PRE, safe), "multi_turn",
       objective="most_data", slots={"objective": "most_data"}),
    _c("m10", ["faturalı 500 TL altı", "20 yaşındayım"], all_(POST, age_ok(20), lambda r: ongoing(r) <= 500),
       "multi_turn", slots={"age": 20, "max_price": 500.0}),
    _c("m11", ["Vodafone faturalı 20 GB", "Yeni bir arama yapalım: faturasız 50 GB"],
       all_(PRE, safe, lambda r: gen(r) >= 50), "multi_turn",
       slots={"operators": None, "min_gb": 50.0, "line_type": "prepaid"}),
    _c("m12", ["Turkcell faturasız", "operatör fark etmez"], all_(PRE, safe), "multi_turn",
       slots={"operators": None, "line_type": "prepaid"}),
    _c("m13", ["faturalı 20 GB", "en az 40 GB olsun"], all_(POST, safe, lambda r: gen(r) >= 40), "multi_turn",
       slots={"min_gb": 40.0}),
    _c("m14", ["faturasız paket", "numaramı taşıyacağım", "otomatik yenilenmesin"],
       all_(PRE, safe, lambda r: channel(r, "number_porting"), lambda r: r["renewal_type"] == "one_time"),
       "multi_turn", slots={"channel": "number_porting", "renewal": "one_time"}),
    _c("m15", ["faturalı 30 GB taahhütsüz", "taahhüt olabilir, en ucuzu"],
       all_(POST, safe, lambda r: gen(r) >= 30), "multi_turn", objective="cheapest",
       slots={"no_commitment": False, "objective": "cheapest"}),
]


def history_for(case: Case):
    """User turns as chat history with a neutral assistant reply between them."""
    from app.models import ChatTurn

    turns = []
    for text in case.turns[:-1]:
        turns.append(ChatTurn(role="user", content=text))
        # A real answer with a citation; its numbers must never become constraints.
        turns.append(ChatTurn(role="assistant", content="1000 TL altı 100 GB paket önerebilirim [S1]."))
    return turns


def expected_top(case: Case, records) -> set[str]:
    """Names that may legitimately rank first (ties included)."""
    if not case.objective:
        return set()
    pool = [r for r in records if case.must(r)]
    if not pool:
        return set()
    key = OBJECTIVES[case.objective]
    best = min(key(r, case.no_commit) for r in pool)
    return {r["name"] for r in pool if key(r, case.no_commit) == best}


# Held-out phrasings, written after the engine was tuned on CASES and run once
# without changes, to estimate how far the rules generalise beyond the tuning set.
HELDOUT: list[Case] = [
    _c("h01", "faturasız hat lazım, en fazla 350 lira", all_(PRE, safe, lambda r: ongoing(r) <= 350), "heldout"),
    _c("h02", "Vodafone'dan faturalı, 25 GB yeter, 600'ü geçmesin",
       all_(op("vodafone"), POST, safe, lambda r: gen(r) >= 25, lambda r: ongoing(r) <= 600), "heldout"),
    _c("h03", "TT faturalı en çok gb hangisinde", all_(op("turk_telekom"), POST, safe), "heldout",
       objective="most_data"),
    _c("h04", "taahhütsüz olsun, faturalı, 60 GB ve üzeri", all_(POST, safe, no_commit_ok, lambda r: gen(r) >= 60),
       "heldout"),
    _c("h05", "Turkcell'e numaramı taşımak istiyorum faturasız 30 GB",
       all_(op("turkcell"), PRE, safe, lambda r: channel(r, "number_porting"), lambda r: gen(r) >= 30), "heldout"),
    _c("h06", "yenilenmeyen bir paket istiyorum faturasız en ucuzu", all_(PRE, safe,
       lambda r: r["renewal_type"] == "one_time"), "heldout", objective="cheapest"),
    _c("h07", "22 yaşında öğrenciyim, faturalı 40 GB", all_(POST, age_ok(22, ("general", "youth")),
       lambda r: gen(r) >= 40), "heldout"),
    _c("h08", "YouTube kotadan yemesin faturalı", all_(POST, safe, lambda r: apps_free(r, "youtube")), "heldout"),
    _c("h09", "faturasız 3 aylık paket en ucuz", all_(PRE, safe), "heldout", objective="cheapest"),
    _c("h10", "Türk Telekom olmasın, faturasız 50 GB", all_(op("turkcell", "vodafone"), PRE, safe,
       lambda r: gen(r) >= 50), "heldout"),
    _c("h11", ["faturalı 20 GB 700 TL altı", "Vodafone olsun", "en ucuzu hangisi"],
       all_(op("vodafone"), POST, safe, lambda r: gen(r) >= 20, lambda r: ongoing(r) <= 700), "heldout",
       objective="cheapest"),
    _c("h12", ["faturasız 40 GB", "bütçem 400 TL"], all_(PRE, safe, lambda r: gen(r) >= 40,
       lambda r: ongoing(r) <= 400), "heldout"),
    _c("h13", "sınırsız internetli faturasız paket var mı 300 TL altı", all_(PRE, safe, lambda r: gen(r) == INF,
       lambda r: ongoing(r) <= 300), "heldout", empty=True),
    _c("h14", "faturalı 2000 dakika ve 30 GB", all_(POST, safe, lambda r: gen(r) >= 30,
       lambda r: r["voice_minutes"] == -1 or r["voice_minutes"] >= 2000), "heldout"),
    _c("h15", "netflix hediyeli faturalı tarife", all_(POST, safe, lambda r: subscription(r, "netflix")), "heldout"),
    _c("h16", "Vodafone faturasız en uzun geçerli paket", all_(op("vodafone"), PRE, safe), "heldout",
       objective="longest_validity"),
    _c("h17", ["Turkcell faturalı", "Vodafone da olabilir", "50 GB"],
       all_(op("turkcell", "vodafone"), POST, safe, lambda r: gen(r) >= 50), "heldout"),
    _c("h18", "aylık 300 TL'yi geçmeyen faturasız paket", all_(PRE, safe, lambda r: (
        p30(r) if days(r) > 31 else ongoing(r)) <= 300), "heldout"),
    _c("h19", "faturalı, taahhüt yok, 1000 TL altı, en çok internet",
       all_(POST, safe, no_commit_ok, lambda r: ongoing(r, True) <= 1000), "heldout", objective="most_data",
       no_commit=True),
    _c("h20", "18 yaşındayım, Vodafone faturalı en ucuz", all_(op("vodafone"), POST, age_ok(18)), "heldout",
       objective="cheapest"),
]
