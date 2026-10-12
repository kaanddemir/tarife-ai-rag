"""Answer grounding: fact blocks, the post-generation guard (with an adversarial
fake LLM), the deterministic fallback, history hygiene and tariff references.
Offline: catalog rows are built from the data files, no database or provider."""
import uuid
from types import SimpleNamespace

import pytest

import app.rag.generate as generate_mod
from app.ingest.load import build_catalog
from app.models import ChatTurn, Package
from app.rag.answer_check import fallback_answer, guard, guard_clarification
from app.rag.facts import comparison_block, fact_block, fmt_tl, price_30d
from app.rag.history import format_history
from app.rag.prompt import answer_style, build_messages
from app.rag.references import names_in, resolve

_COLUMNS = {c.name for c in Package.__table__.columns}
_RECORDS = {r["name"]: r for r in build_catalog()[0]}


def pkg(name: str, **extra) -> Package:
    p = Package(id=uuid.uuid4(), **{k: v for k, v in _RECORDS[name].items() if k in _COLUMNS and k != "id"})
    for k, v in extra.items():
        setattr(p, k, v)
    return p


RED60 = "5G Red 60GB Sinirsiz Eglence Tarifesi"     # 60 GB general + unlimited social, committed
PRIME_NETFLIX = "Prime Netflix 30 GB"                # Netflix subscription is a free trial
HOS_GELDIN = "Hoş Geldin 50 GB Paketi"               # 300 TL / 90 days, one-time, new line only
UNLIMITED = "Yeni Nesil Prime Sınırsız"              # unlimited general internet


# ---- formatting and calculated values ----

def test_money_format_and_30_day_value():
    assert fmt_tl(1250) == "1.250 TL" and fmt_tl(99.9) == "99,90 TL" and fmt_tl(300.0) == "300 TL"
    assert price_30d(pkg(HOS_GELDIN)) == 100
    assert price_30d(pkg(RED60)) is None  # postpaid: the monthly price is the basis


def test_fact_block_separates_official_calculated_conditional_unknown():
    block = fact_block("S1", pkg(HOS_GELDIN))
    assert "RESMİ" in block and "300 TL, tek ödemeyle 90 gün geçerli" in block
    assert "aylık fatura değildir" in block
    assert "HESAP (TarifeAI, yalnızca kıyas için; operatör böyle faturalamaz): 30 günlük karşılığı yaklaşık 100 TL" in block
    assert "tek seferlik; otomatik yenilenmez" in block
    red = fact_block("S1", pkg(RED60))
    assert "60 GB genel + sinirsiz sosyal medya internet (genel internet sinirsiz degil)" in red
    assert "Taahhütsüz fiyat: 2.780 TL / ay" in red
    netflix = fact_block("S1", pkg(PRIME_NETFLIX))
    assert "ucretsiz deneme" in netflix and "BİLİNMİYOR" in netflix


def test_fact_block_shows_requirement_verdicts_and_compromise():
    p = pkg(RED60, retrieval_status="compromise", retrieval_checks=[("en fazla 700 TL", False), ("faturalı", True)])
    block = fact_block("S2", p)
    assert "UZLAŞMA ALTERNATİFİ" in block and "en fazla 700 TL: KARŞILAMIYOR" in block


def test_comparison_uses_one_basis():
    block = comparison_block([pkg(HOS_GELDIN), pkg(RED60)], ["S1", "S2"])
    assert "[S1] 30 günlük kıyas: 100 TL" in block and "[S2] 30 günlük kıyas: 1.000 TL, 12 ay taahhütlü" in block
    assert "en düşük: [S1]" in block


# ---- guard: adversarial model output ----

@pytest.mark.parametrize("bad", [
    "Bu paket 99 TL'ye sınırsız internet sunuyor [S1].",                       # invented price + unlimited
    "Netflix ömür boyu bedava [S1].",                                           # lifelong trial
    "60 GB + sınırsız = 160 GB internet [S1].",                                # invented total
    "Ayrıntılar için https://uydurma.example.com adresine bak [S1].",           # invented URL
])
def test_guard_removes_unsupported_claims(bad):
    good = "Vodafone 5G Red 60GB, 1.000 TL / ay ve 60 GB genel internet sunuyor [S1]."
    result = guard(f"{good}\n{bad}\n{good}", [pkg(RED60)], "faturalı 60 GB")
    assert result.violations
    assert "99 TL" not in result.answer and "ömür boyu" not in result.answer
    assert "160 GB" not in result.answer and "uydurma" not in result.answer
    assert "1.000 TL" in result.answer


def test_guard_keeps_correct_restricted_unlimited_wording():
    text = "60 GB genel internetin yanında sosyal medya uygulamalarında sınırsız internet var [S1]."
    assert guard(text, [pkg(RED60)], "q").violations == []


def test_guard_trial_must_be_called_a_trial():
    p = pkg(PRIME_NETFLIX)
    bad = guard("Netflix üyeliği hediye [S1]. Fiyatı 720 TL [S1].", [p], "q")
    assert any("trial" in v for v in bad.violations)
    ok = guard("Netflix Temel Paket ücretsiz deneme olarak geliyor [S1].", [p], "q")
    assert ok.violations == []


def test_guard_drops_unknown_citations_and_misattributed_prices():
    result = guard("Fiyat 1.000 TL [S1]. Diğeri 300 TL [S1]. Bir de [S7] var.", [pkg(RED60)], "q")
    assert "[S7]" not in result.answer and "300 TL" not in result.answer


def test_guard_completes_a_compromise_sentence_and_adds_status_header():
    p = pkg(RED60, retrieval_status="compromise", retrieval_checks=[("en fazla 700 TL", False)])
    result = guard("Vodafone 5G Red 60GB 1.000 TL [S1].", [p], "700 TL altı")
    assert "şu şartını karşılamıyor: en fazla 700 TL" in result.answer
    assert result.answer.startswith("**Şartlarının tamamını karşılayan paket bulamadım")


def test_guard_falls_back_when_most_of_the_answer_is_unsupported():
    result = guard("Bu 99 TL [S1]. Şu 88 TL [S1]. Tamam [S1].", [pkg(RED60)], "q")
    assert result.fallback and "1.000 TL" in result.answer and "[S1]" in result.answer


def test_fallback_answer_is_grounded_and_labels_compromises():
    text = fallback_answer([pkg(HOS_GELDIN), pkg(RED60, retrieval_status="compromise",
                                                    retrieval_checks=[("faturasız", False)])])
    assert "300 TL, 90 gün geçerli (kıyas için 30 günlük karşılığı yaklaşık 100 TL)" in text
    assert "[S2] şu şartını karşılamıyor: faturasız" in text


def test_clarification_cannot_invent_price_bands():
    assert "TL" not in guard_clarification("Bütçen 150 TL altı mı?", ["budget"])
    assert guard_clarification("Faturalı mı faturasız mı?", ["line_type"]) == "Faturalı mı faturasız mı?"


# ---- end to end through generate_answer with an adversarial fake model ----

class _Fake:
    def __init__(self, text):
        self.text = text

    def create(self, **kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))])


def test_generate_answer_never_returns_a_known_false_claim(monkeypatch):
    fake = SimpleNamespace(chat=SimpleNamespace(completions=_Fake(
        "**Öneri:** Yeni Nesil Prime Sınırsız tamamen sınırsız [S1]. Vodafone Red sınırsız internet, 500 TL [S2].")))
    monkeypatch.setattr(generate_mod, "_client", lambda: fake)
    resp = generate_mod.generate_answer("sınırsız internet", [pkg(UNLIMITED), pkg(RED60)])
    # Half the sentences are false -> the grounded fallback replaces the answer.
    assert "500 TL" not in resp.answer and "Vodafone Red sınırsız internet" not in resp.answer
    assert "genel internet sinirsiz degil" in resp.answer
    assert [c.id for c in resp.citations] == ["S1", "S2"]


# ---- prompt hygiene ----

def test_history_markers_are_stripped_to_avoid_id_collisions():
    text = format_history([ChatTurn(role="user", content="x"),
                           ChatTurn(role="assistant", content="A paketi [S1] ve B [S2-S3].")])
    assert "[S" not in text


def test_answer_style_adapts_to_question():
    assert "Kisa" in answer_style("En ucuz hangisi?")
    assert "Detayli" in answer_style("İkisini detaylı karşılaştır")
    assert "Uygunluk" in answer_style("Numaramı taşıyabilir miyim")
    assert "sinirsizligi" in answer_style("Sınırsız internet mi?")
    assert "kosullari" in answer_style("Bu hediyenin koşulları ne?")


def test_prompt_is_bounded_to_six_sources():
    many = [pkg(n) for n in list(_RECORDS)[:9]]
    messages, citations = build_messages("q", many)
    assert len(citations) == 6 and "[S7]" not in messages[1]["content"]


# ---- references to tariffs ----

def test_named_tariff_is_found_with_turkish_letters_and_without_suffix():
    assert names_in("Hoş Geldin 50 GB paketinin koşulları ne?") == [HOS_GELDIN]
    assert names_in("hos geldin 50 gb") == [HOS_GELDIN]


def test_ordinal_reference_resolves_against_the_last_answer():
    history = [ChatTurn(role="user", content="faturalı sınırsız"),
               ChatTurn(role="assistant", content=f"Önerim {UNLIMITED} [S1], alternatif {RED60} [S2].")]
    assert resolve("İkinci tarifeyi detaylı anlat", history) == ([RED60], None)


def test_ambiguous_reference_asks_instead_of_guessing():
    names, note = resolve("önceki tarifeyle karşılaştır", [ChatTurn(role="assistant", content="Merhaba!")])
    assert names == [] and "Tahmin etme" in note


# ---- API contract for the UI ----

def test_package_out_exposes_match_verdict_and_verification_date():
    from datetime import datetime, timezone

    from app.models import PackageOut

    p = pkg(RED60, retrieval_status="compromise", retrieval_checks=[("en fazla 700 TL", False), ("faturalı", True)],
            verified_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    out = PackageOut.model_validate(p).model_dump(mode="json")
    assert out["retrieval_status"] == "compromise"
    assert out["retrieval_checks"] == [["en fazla 700 TL", False], ["faturalı", True]]
    assert out["verified_at"].startswith("2026-09-01")
    # Rows outside retrieval (e.g. /compare) carry no verdict rather than a made-up one.
    plain = PackageOut.model_validate(pkg(HOS_GELDIN)).model_dump(mode="json")
    assert plain["retrieval_status"] is None and plain["retrieval_checks"] is None


# ---- Unlimited wording, operator names, comparisons ----

PRIME75 = "Yeni Nesil Prime 75 GB"   # unlimited social media + WhatsApp/BiP/YaaY app data, finite general
PARLAMENTER = "Yeni Parlamenter 5 GB"  # no app-scoped allowance at all


@pytest.mark.parametrize("text", [
    "WhatsApp'ta sınırsız internet var [S1].",
    "Sosyal medya uygulamalarında sınırsız internet sunuyor [S1].",
    "Sınırsız sosyal medya interneti var [S1].",
    "Genel internet sınırsız değil, ancak sosyal medya sınırsız [S1].",
])
def test_guard_keeps_true_app_scoped_unlimited_claims(text):
    assert guard(text, [pkg(PRIME75)], "q").violations == []


@pytest.mark.parametrize("text", [
    "Bu tarifenin bütün interneti sınırsız [S1].",
    "İnterneti sınırsız [S1].",
    "Tüm internet kullanımın sınırsız [S1].",
    "Kota derdi yok, internet tamamen sınırsız [S1].",       # negation in another clause
    "Sınırsız internetle WhatsApp'ta da rahat edersin [S1].",
])
def test_guard_rejects_general_unlimited_when_only_apps_are_unlimited(text):
    assert "unlimited general internet claimed" in guard(text, [pkg(PRIME75)], "q").violations


@pytest.mark.parametrize("text", ["WhatsApp'ta sınırsız internet var [S1].",
                                  "Sosyal medya uygulamalarında sınırsız internet sunuyor [S1]."])
def test_guard_rejects_app_scoped_unlimited_the_source_does_not_have(text):
    assert "unlimited app data not in the source" in guard(text, [pkg(PARLAMENTER)], "q").violations


def test_guard_rejects_app_unlimited_for_an_app_outside_the_allowance():
    # Prime 75's unlimited social apps are Instagram/Facebook/YouTube/X/...; TikTok is not one.
    assert guard("TikTok'ta sınırsız internet var [S1].", [pkg(PRIME75)], "q").violations


def test_guard_rejects_wrong_operator_for_the_cited_source():
    result = guard("Turkcell'in 5G Red 60GB tarifesi 1.000 TL [S1].", [pkg(RED60)], "q")
    assert "operator does not match the cited source" in result.violations
    assert guard("Turkcell yerine Vodafone 5G Red 60GB, 1.000 TL [S1].", [pkg(RED60)], "q").violations == []


@pytest.mark.parametrize("text, bad", [
    ("Hoş Geldin 50 GB, 30 günlük kıyasta en ucuz seçenek [S2].", False),
    ("5G Red 60GB en ucuz seçenek [S1].", True),
    ("Hoş Geldin 50 GB, 5G Red'den daha fazla internet veriyor [S2].", True),
    ("5G Red 60GB daha fazla internet veriyor [S1].", False),
    ("5G Red 60GB daha ucuz [S1].", True),
])
def test_guard_checks_comparisons_against_the_deterministic_basis(text, bad):
    result = guard(text, [pkg(RED60), pkg(HOS_GELDIN)], "q")
    assert bool([v for v in result.violations if "compar" in v or "superlative" in v]) == bad
