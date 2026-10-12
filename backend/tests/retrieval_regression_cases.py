"""Additional retrieval regression cases.

Fresh wording (numbers in words, slang, negation, conflicting constraints, anaphora,
exclusions, unavailable benefits, eligibility + price). Ground truth uses the same
fact predicates as ``retrieval_benchmark_cases`` and is computed over the catalog, not
over retrieval output.

Held-out cases were not used to change retrieval code. Any case that later drives a
fix is moved to the development set and must not be reported as held-out evidence.
"""
from __future__ import annotations

from retrieval_benchmark_cases import (INF, POST, PRE, _c, age_ok, all_, apps_free, aud, channel, gen,
                                       no_commit_ok, ongoing, op, p30, roaming, safe, subscription)

REGRESSION_HELDOUT = [
    # numbers written out / vague budgets
    _c("p01", "faturasız, iki yüz elli lirayı geçmesin", all_(PRE, safe, lambda r: ongoing(r) <= 250), "p5_numeric"),
    _c("p02", "faturalı bir tarife, yüz gigadan fazla internet",
       all_(POST, safe, lambda r: gen(r) >= 100), "p5_numeric"),
    _c("p03", "bin liranın altında faturalı, otuz gb yeter",
       all_(POST, safe, lambda r: ongoing(r) <= 1000, lambda r: gen(r) >= 30), "p5_numeric"),
    _c("p04", "faturalı en ucuz tarife hangisi", all_(POST, safe), "p5_objective", objective="cheapest"),
    _c("p05", "faturasızda en bol internet veren paket", all_(PRE, safe), "p5_objective", objective="most_data"),
    # slang / spelling
    _c("p06", "turkcel faturasiz 20gb lazim", all_(op("turkcell"), PRE, safe, lambda r: gen(r) >= 20), "p5_spelling"),
    _c("p07", "vodafon faturali sinirsiz net", all_(op("vodafone"), POST, safe, lambda r: gen(r) == INF),
       "p5_spelling", empty=True),
    # negation / exclusions
    _c("p08", "Turkcell hariç faturalı 40 GB", all_(op("vodafone", "turk_telekom"), POST, safe,
       lambda r: gen(r) >= 40), "p5_exclusion"),
    _c("p09", "Vodafone istemiyorum, faturasız paket öner", all_(op("turkcell", "turk_telekom"), PRE, safe),
       "p5_exclusion"),
    _c("p10", "taahhüt vermek istemiyorum, faturalı 30 GB", all_(POST, safe, no_commit_ok,
       lambda r: gen(r) >= 30), "p5_commitment"),
    # renewal vs commitment
    _c("p11", "her ay kendiliğinden yenilenmesin, faturasız", all_(PRE, safe,
       lambda r: r["renewal_type"] == "one_time"), "p5_renewal"),
    _c("p12", "otomatik yenilenen faturasız paket 30 GB", all_(PRE, safe,
       lambda r: r["renewal_type"] == "recurring", lambda r: gen(r) >= 30), "p5_renewal"),
    # conflicting / impossible constraints
    _c("p13", "faturasız sınırsız internet 100 TL", all_(PRE, safe, lambda r: gen(r) == INF,
       lambda r: ongoing(r) <= 100), "p5_no_match", empty=True),
    _c("p14", "faturalı 500 GB internet 300 TL altı", all_(POST, safe, lambda r: gen(r) >= 500,
       lambda r: ongoing(r) <= 300), "p5_no_match", empty=True),
    _c("p15", "Türk Telekom faturalı Disney+ hediyeli", all_(op("turk_telekom"), POST, safe,
       lambda r: subscription(r, "disney")), "p5_benefit", empty=True),
    # app / restricted allowances
    _c("p16", "WhatsApp kotamdan düşmesin, faturasız", all_(PRE, safe, lambda r: apps_free(r, "whatsapp")),
       "p5_restricted"),
    _c("p17", "instagram ve youtube sınırsız olsun faturalı", all_(POST, safe,
       lambda r: apps_free(r, "instagram") and apps_free(r, "youtube")), "p5_restricted"),
    _c("p18", "yurt dışında da kullanabileceğim faturalı tarife", all_(POST, safe, roaming), "p5_benefit"),
    # eligibility + price
    _c("p19", "20 yaşındayım, faturasız 300 TL altı", all_(PRE, age_ok(20), lambda r: ongoing(r) <= 300),
       "p5_eligibility"),
    _c("p20", "emekliyim, faturalı uygun tarife", all_(POST, aud("general", "retired")), "p5_eligibility"),
    _c("p21", "12 yaşındaki çocuğuma hat, faturasız", all_(PRE, age_ok(12, ("general", "child"))),
       "p5_eligibility"),
    _c("p22", "milletvekili değilim, faturalı 50 GB en ucuz", all_(POST, safe, lambda r: gen(r) >= 50),
       "p5_eligibility", objective="cheapest"),
    # activation channel
    _c("p23", "numara taşıma ile Vodafone faturalı", all_(op("vodafone"), POST, safe,
       lambda r: channel(r, "number_porting")), "p5_channel"),
    # mixed prepaid/postpaid
    _c("p24", "faturalı ya da faturasız fark etmez, 30 günlük maliyeti en düşük 20 GB",
       all_(safe, lambda r: gen(r) >= 20), "p5_mixed", objective="cheapest"),
    # multi-turn / anaphora
    _c("p25", ["faturasız 40 GB", "bunların en ucuzu"], all_(PRE, safe, lambda r: gen(r) >= 40), "p5_multiturn",
       objective="cheapest"),
    _c("p26", ["Vodafone faturalı 30 GB", "Turkcell'de de var mı aynısı"],
       all_(op("turkcell"), POST, safe, lambda r: gen(r) >= 30), "p5_multiturn"),
    _c("p27", ["faturalı 700 TL altı", "biraz daha internet olsun, 40 GB"],
       all_(POST, safe, lambda r: ongoing(r) <= 700, lambda r: gen(r) >= 40), "p5_multiturn"),
    _c("p28", ["Türk Telekom faturasız", "bütçem aslında 250 lira"],
       all_(op("turk_telekom"), PRE, safe, lambda r: ongoing(r) <= 250), "p5_multiturn"),
    _c("p29", ["faturalı sınırsız internet", "taahhütsüz olanı var mı"],
       all_(POST, safe, no_commit_ok, lambda r: gen(r) == INF), "p5_multiturn"),
    # 30-day equivalent budgets for long-validity packages
    _c("p30", "faturasız, aylık maliyeti 150 lirayı aşmasın", all_(PRE, safe, lambda r: p30(r) <= 150),
       "p5_numeric"),
]

# Cases that influenced a fix (spoken numbers, "giga", "sınırsız net"):
# Moved out of the held-out set after the first run, so they are development cases now.
_DEV_IDS = {"p01", "p02", "p03", "p07"}
REGRESSION_DEV = [c for c in REGRESSION_HELDOUT if c.id in _DEV_IDS] + [
    # From the second batch: drove the negated-minimum fix ("...den fazla vermem").
    _c("q01", "faturasız paket, üç yüz liradan fazla vermem", all_(PRE, safe, lambda r: ongoing(r) <= 300),
       "p5b_numeric"),
]
REGRESSION_HELDOUT = [c for c in REGRESSION_HELDOUT if c.id not in _DEV_IDS]
# Note on p21: every child tariff is postpaid; the engine returns them as labelled
# compromises rather than confirming general prepaid lines for a 12-year-old. The
# ground truth here (general prepaid counts) is debatable, so p21 is reported, not fixed.

# Second batch, written after the spoken-number fix and before running it: checks that
# the fix generalizes instead of matching the dev wording.
EXTRA_HELDOUT = [
    _c("q02", "Turkcell faturalı elli giga", all_(op("turkcell"), POST, safe, lambda r: gen(r) >= 50),
       "p5b_numeric"),
    _c("q03", "aylık sekiz yüz lirayı geçmeyen faturalı, kırk gb", all_(POST, safe, lambda r: ongoing(r) <= 800,
       lambda r: gen(r) >= 40), "p5b_numeric"),
    _c("q04", "türk telekom sınırsız net faturalı", all_(op("turk_telekom"), POST, safe, lambda r: gen(r) == INF),
       "p5b_spelling"),
    _c("q05", "faturasız yirmi beş giga en ucuz", all_(PRE, safe, lambda r: gen(r) >= 25), "p5b_objective",
       objective="cheapest"),
    _c("q06", ["faturalı paket arıyorum", "bin iki yüz lirayı geçmesin", "altmış gb olsun"],
       all_(POST, safe, lambda r: ongoing(r) <= 1200, lambda r: gen(r) >= 60), "p5b_multiturn"),
    _c("q07", "vodafone dışında faturasız on gb", all_(op("turkcell", "turk_telekom"), PRE, safe,
       lambda r: gen(r) >= 10), "p5b_exclusion"),
    _c("q08", "taahhütsüz faturalı, beş yüz lira altı", all_(POST, safe, no_commit_ok,
       lambda r: ongoing(r, True) <= 500), "p5b_commitment"),
    _c("q09", "faturasız sınırsız net iki yüz lira", all_(PRE, safe, lambda r: gen(r) == INF,
       lambda r: ongoing(r) <= 200), "p5b_no_match", empty=True),
    _c("q10", "18 yaşındayım faturalı yüz gb", all_(POST, age_ok(18, ("general", "youth")),
       lambda r: gen(r) >= 100), "p5b_eligibility"),
]
