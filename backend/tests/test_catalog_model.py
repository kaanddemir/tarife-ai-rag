"""Canonical model: structured benefits, explicit unknowns, and the facts the
2026-10-12 official-source pass added or corrected. Offline (no database)."""
import copy

import pytest

from app.ingest.load import build_catalog, load_records
from app.ingest.normalize import UNLIMITED, benefit_phrase, benefit_tags, benefits_sentence, normalize_record
from app.ingest.validate import ValidationReport, validate_catalog, validate_record


@pytest.fixture(scope="module")
def catalog():
    records, _ = build_catalog()
    return {(r["operator"], r["line_type"], r["name"]): r for r in records}


def _rec(catalog, operator, line_type, name):
    return catalog[(operator, line_type, name)]


def _errors(rec) -> list[str]:
    report = ValidationReport()
    validate_record(rec, report)
    return report.errors


# ---- benefit semantics -----------------------------------------------------------

def test_zero_rated_netflix_data_is_not_a_netflix_subscription(catalog):
    """Vodafone Red: 'Netflix ... kullanımları sınırsızdır' is data, not membership."""
    red = _rec(catalog, "vodafone", "postpaid", "5G Red 60GB Sinirsiz Eglence Tarifesi")
    assert "netflix" not in benefit_tags(red) and "streaming_perks" not in benefit_tags(red)
    data = [b for b in red["benefits"] if b["category"] == "app_data"]
    assert data and "Netflix" in data[0]["apps"] and data[0]["amount_gb"] == UNLIMITED
    assert not any(b["category"] == "subscription" for b in red["benefits"])
    assert "uygulama aboneligi degil" in red["doc_text"]


def test_netflix_trial_is_a_trial_that_becomes_paid(catalog):
    for gb, after in (("30", 259.99), ("40", 389.99), ("60", 499.99)):
        rec = _rec(catalog, "turk_telekom", "postpaid", f"Prime Netflix {gb} GB")
        netflix = next(b for b in rec["benefits"] if "Netflix" in b["service"])
        assert (netflix["category"], netflix["offer"], netflix["duration_months"]) == ("subscription", "free_trial", 6)
        assert netflix["after_trial"] == "paid" and netflix["after_trial_price_try"] == after
        assert {"netflix", "free_trial_subscription"} <= set(benefit_tags(rec))
        assert "ilk 6 ay ucretsiz deneme, sonrasinda ucretli" in rec["doc_text"]


def test_turkcell_app_data_requires_buying_the_service(catalog):
    for (op, lt, name), rec in catalog.items():
        if op != "turkcell" or lt != "postpaid":
            continue
        app_data = {b["apps"][0]: b for b in rec["benefits"] if b["category"] == "app_data" and b["apps"][0] in ("fizy", "TV+", "Lifebox")}
        assert set(app_data) == {"fizy", "TV+", "Lifebox"}, name
        assert all(b["offer"] == "requires_purchase" for b in app_data.values()), name
        assert "servis ayrica satin alinmadan kullanilamaz" in rec["doc_text"], name
        assert not any(b["category"] == "subscription" for b in rec["benefits"]), name


def test_new_line_only_bonus_is_labelled(catalog):
    for name in ("Yeni Nesil Prime 75 GB", "Yeni Nesil Prime Sınırsız"):
        rec = _rec(catalog, "turk_telekom", "postpaid", name)
        bonus = next(b for b in rec["benefits"] if b["category"] == "bonus_data")
        assert bonus["eligibility"] == "new_line_only" and bonus["amount_gb"] == 10
        assert "[yalnizca yeni hat ile gelenlere]" in rec["doc_text"]
        # A conditional bonus never inflates the tariff's own allowance.
        assert rec["bonus_internet_gb"] is None


def test_unverified_and_none_are_distinguishable(catalog):
    statuses = {}
    for rec in catalog.values():
        statuses.setdefault(rec["benefits_status"], []).append(rec)
    assert set(statuses) == {"verified", "verified_none", "unverified"}
    for rec in statuses["unverified"]:
        assert rec["benefits"] == [] and "dogrulanamadi" in rec["doc_text"]
    for rec in statuses["verified_none"]:
        assert rec["benefits"] == [] and "ek fayda belirtilmemis" in rec["doc_text"]
    for rec in statuses["verified"]:
        assert rec["benefits"]


def test_unknown_channel_is_null_never_empty_or_guessed(catalog):
    for rec in catalog.values():
        assert rec["activation_channels"] is None or rec["activation_channels"], rec["name"]


@pytest.mark.parametrize(
    "benefit, fragment",
    [
        ({"category": "subscription", "service": "X", "offer": "free_trial", "after_trial": "unknown"}, "needs duration_months"),
        ({"category": "subscription", "service": "X", "offer": "free_trial", "duration_months": 3}, "needs after_trial"),
        ({"category": "subscription", "service": "X", "offer": "requires_purchase"}, "requires_purchase only"),
        ({"category": "app_data", "service": "X", "offer": "included", "amount_gb": 5}, "must name its apps"),
        ({"category": "app_data", "service": "X", "offer": "included", "apps": ["X"], "amount_gb": 0}, "amount_gb"),
        ({"category": "voucher", "service": "X", "offer": "included"}, "value_try"),
        ({"category": "gift", "service": "X", "offer": "included"}, "category="),
        ({"category": "service", "service": "X", "offer": "free"}, "offer="),
        ({"category": "service", "service": "X", "offer": "included", "after_trial": "paid"}, "only applies to a free_trial"),
        ({"category": "service", "service": "X", "offer": "included", "price": 5}, "unknown key"),
    ],
)
def test_benefit_shape_rules(catalog, benefit, fragment):
    rec = copy.deepcopy(_rec(catalog, "turkcell", "postpaid", "5G Ultra 10 GB"))
    rec["benefits"] = [benefit]
    assert any(fragment in e for e in _errors(rec)), _errors(rec)


@pytest.mark.parametrize(
    "status, benefits, fragment",
    [
        ("verified", [], "at least one benefit"),
        ("verified_none", [{"category": "service", "service": "X", "offer": "included"}], "cannot list benefits"),
        ("unverified", [{"category": "service", "service": "X", "offer": "included"}], "cannot list benefits"),
        ("maybe", [], "benefits_status="),
    ],
)
def test_benefit_status_rules(catalog, status, benefits, fragment):
    rec = copy.deepcopy(_rec(catalog, "turkcell", "postpaid", "5G Ultra 10 GB"))
    rec["benefits"], rec["benefits_status"] = benefits, status
    assert any(fragment in e for e in _errors(rec))


def test_catalog_records_must_state_benefits_and_channels():
    raw = load_records()
    del raw[0]["benefits_status"]
    with pytest.raises(Exception, match="benefits_status is required"):
        build_catalog(raw)


def test_netflix_tag_requires_a_netflix_subscription(catalog):
    """Tags are derived, so a Netflix tag can only come from a Netflix subscription benefit."""
    rec = copy.deepcopy(_rec(catalog, "vodafone", "postpaid", "5G Red 100GB Sinirsiz Tiktok Tarifesi"))
    assert "netflix" not in benefit_tags(rec)
    rec["benefits"] = rec["benefits"] + [{"category": "subscription", "service": "Netflix", "offer": "free_trial",
                                          "duration_months": 6, "after_trial": "paid"}]
    assert "netflix" in benefit_tags(rec)


def test_benefit_tags_are_derived_not_stored(catalog):
    """Every catalog tag is reproducible from `benefits` + structured fields alone."""
    from app.ingest.normalize import _field_benefit_tags, _structured_benefit_tags
    for rec in catalog.values():
        assert "benefit_tags" not in rec
        expected = _structured_benefit_tags(rec) | _field_benefit_tags(rec)
        if any(b["category"] == "bonus_data" for b in rec["benefits"]):
            expected.add("bonus_data")
        assert set(benefit_tags(rec)) == expected, rec["name"]
        # Marketing text alone never adds a tag to a catalog record.
        assert set(benefit_tags({**rec, "campaign": "Netflix 5G aşımsız sınırsız eğlence"})) == expected


def test_channel_and_promo_rules(catalog):
    base = _rec(catalog, "turk_telekom", "postpaid", "Ailece Başlangıç 100 MB")
    for overrides, fragment in [
        ({"activation_channels": []}, "activation_channels="),
        ({"activation_channels": ["store"]}, "activation_channels="),
        ({"promo_months": None}, "go together"),
        ({"price_after_promo_try": 90}, "must exceed"),
        ({"social_media_apps": ["Instagram"]}, "without a social_media_gb"),
    ]:
        assert any(fragment in e for e in _errors({**base, **overrides})), overrides


def test_phrases_never_upgrade_a_benefit():
    assert "ucretsiz" not in benefit_phrase({"category": "discount", "service": "Vitamin %35", "offer": "discounted"}).replace("ucretsiz degil", "")
    phrase = benefit_phrase({"category": "subscription", "service": "ChatGPT Go", "offer": "free_trial",
                             "duration_months": 3, "after_trial": "unknown"})
    assert "ilk 3 ay ucretsiz deneme" in phrase and "sonrasi kaynakta belirtilmemis" in phrase
    assert benefits_sentence({}) is None  # pre-2D record: no claim either way


def test_structured_benefits_override_text_matching():
    """Campaign text mentioning Netflix no longer creates a Netflix tag once benefits are structured."""
    rec = {"operator": "vodafone", "line_type": "postpaid", "customer_type": "new", "name": "T",
           "price_try": 100, "price_period": "month", "commitment_months": 12, "internet_gb": 10,
           "voice_minutes": 100, "source_url": "https://x",
           "campaign": "Netflix ve YouTube Premium kullanımları sınırsızdır.",
           "benefits": [], "benefits_status": "verified_none", "activation_channels": None}
    assert not {"netflix", "streaming_perks"} & set(benefit_tags(normalize_record(rec)))
    legacy = {k: v for k, v in rec.items() if k not in ("benefits", "benefits_status")}
    assert "netflix" in benefit_tags(normalize_record(legacy))  # old path unchanged


# ---- facts added or corrected on 2026-10-12 --------------------------------------

def test_turkcell_postpaid_price_protection_is_recorded(catalog):
    turkcell = [r for (op, lt, _), r in catalog.items() if op == "turkcell" and lt == "postpaid"]
    assert len(turkcell) == 24 and all(r["price_lock_months"] == 12 for r in turkcell)


def test_turk_telekom_committed_tariffs_carry_their_no_commitment_price(catalog):
    expected = {"Yeni Parlamenter 5 GB": 460, "Enerji 25 GB + 5 GB Sosyal Medya": 1060,
                "Ailece Prime Düşün 100 GB": 2160, "Prime Netflix 40 GB": 1894.60}
    for name, price in expected.items():
        assert _rec(catalog, "turk_telekom", "postpaid", name)["no_commitment_price_try"] == price
    committed = [r for (op, lt, _), r in catalog.items()
                 if op == "turk_telekom" and lt == "postpaid" and r["commitment_status"] == "committed"]
    assert committed and all(r["no_commitment_price_try"] for r in committed)


def test_ailece_baslangic_first_month_price_is_marked_introductory(catalog):
    rec = _rec(catalog, "turk_telekom", "postpaid", "Ailece Başlangıç 100 MB")
    assert (rec["price_try"], rec["promo_months"], rec["price_after_promo_try"]) == (100, 1, 190)
    assert "Bu fiyat ilk 1 ay icindir; sonrasinda 190 TL'dir." in rec["doc_text"]


def test_prime_netflix_social_quota_names_its_apps(catalog):
    rec = _rec(catalog, "turk_telekom", "postpaid", "Prime Netflix 30 GB")
    assert rec["social_media_gb"] == UNLIMITED and rec["social_media_apps"] == ["X", "Facebook"]
    assert rec["internet_gb"] == 30 and "unlimited_data" not in benefit_tags(rec)


def test_memlekete_corrections(catalog):
    forty = _rec(catalog, "vodafone", "prepaid", "Memlekete Hoş Geldin 40GB Paketi")
    assert forty["validity_days"] == 90 and "28 gün" not in forty["conditions"]
    for name in ("Memlekete Hoş Geldin 80GB Paketi", "Memlekete Hoş Geldin 100GB Paketi"):
        rec = _rec(catalog, "vodafone", "prepaid", name)
        assert {"dealer_only", "province_restricted"} <= set(rec["eligibility_tags"])
        assert "Antalya, Aydın, Edirne, İzmir, Kayseri, Konya, Muğla, Nevşehir" in rec["conditions"]
        assert "Adana" not in rec["conditions"]


def test_unconfirmed_campaign_claims_were_removed(catalog):
    for (op, lt, name), rec in catalog.items():
        if op == "turkcell":
            assert "Biletinial" not in (rec.get("campaign") or ""), name
            assert "sosyal medyada geçerli 5 GB hediye" not in (rec.get("campaign") or ""), name


def test_every_changed_fact_keeps_its_previous_value(catalog):
    raw = {(r["operator"], r["line_type"], r["name"]): r for r in load_records()}
    changed = {k for k, r in raw.items() if any(h["as_of"] == "2026-10-12" for h in r.get("history", []))}
    assert ("turkcell", "postpaid", "5G Ultra 10 GB") in changed  # price lock None -> 12
    assert ("vodafone", "prepaid", "Memlekete Hoş Geldin 40GB Paketi") in changed
    assert len(changed) == 40  # 34 initial updates + Genç Red x2 and Dijital x4


def test_enriched_catalog_still_validates(catalog):
    validate_catalog(list(catalog.values()))


def test_documents_fit_the_embedding_window(catalog):
    """BGE-M3 encodes max_length=1024 tokens; the longest document measured 888 tokens
    (2,991 chars, ~3.4 chars/token). 3,300 chars keeps a margin so nothing is truncated."""
    assert max(len(r["doc_text"]) for r in catalog.values()) < 3300


def test_quality_reports_are_never_ingested():
    from app.ingest.load import DATA_DIR
    assert not list(DATA_DIR.glob("*.md")) and not list(DATA_DIR.glob("*.csv"))


# ---- Canonical field regressions ----

def test_bonus_internet_says_where_it_works(catalog):
    """bonus_internet_gb used to mix general gift data, hotspot-only and app-only data."""
    scopes = {}
    for (op, lt, name), rec in catalog.items():
        assert (rec.get("bonus_internet_gb") is None) == (rec.get("bonus_internet_scope") is None), name
        if rec.get("bonus_internet_scope"):
            scopes[name] = rec["bonus_internet_scope"]
    assert scopes["Memlekete Hoş Geldin 40GB Paketi"] == "tethering_only"
    assert scopes["5G Cepte Fırsat 10"] == "app_specific"
    assert scopes["5G Plus Genç 10GB Tarifesi"] == "general"
    hotspot = catalog[("vodafone", "prepaid", "Memlekete Hoş Geldin 40GB Paketi")]["doc_text"]
    assert "yalnizca internet paylasiminda" in hotspot and "bonus/paylasim" not in hotspot


def test_genc_red_gift_is_general_data_not_a_social_quota(catalog):
    """Official card: "+ 40 GB HEDİYE", "Tüm internet kullanımlarında geçerli"."""
    for name, gb in (("5G Genç Red 80 GB Sinirsiz Sosyal Tarifesi", 40), ("5G Genç Red 120GB Sinirsiz Eglence Tarifesi", 60)):
        rec = catalog[("vodafone", "postpaid", name)]
        assert rec["internet_gb"] == gb and rec["bonus_internet_gb"] == gb
        assert rec["bonus_internet_scope"] == "general"
        assert "oyun interneti" not in (rec["campaign"] + rec["conditions"])


def test_tt_dijital_channels_resolved_from_official_page(catalog):
    for gb in (4, 8, 12, 24):
        rec = catalog[("turk_telekom", "prepaid", f"Dijital {gb} GB")]
        assert rec["activation_channels"] == ["new_line", "number_porting"]
        assert "numara taşıyarak" in rec["evidence"]
