"""Offline ingestion/validation tests (no database, no embedding model)."""
import copy
import json
import math

import pytest

import app.ingest.load as load_mod
from app.ingest.load import build_catalog, load_records, package_id, run
from app.ingest.normalize import UNLIMITED, activation_channels, benefit_tags, channel_phrase, normalize_record
from app.ingest.validate import (
    CatalogValidationError,
    ValidationReport,
    validate_provenance,
    validate_embeddings,
    validate_record,
)


def _by_name(records, name):
    return next(r for r in records if r["name"] == name)


def _errors_for(rec: dict) -> list[str]:
    report = ValidationReport()
    validate_record(normalize_record(rec, require_mobile_voice=True), report)
    return report.errors


@pytest.fixture(scope="module")
def raw():
    return load_records()


# ---- the real catalog ----

def test_real_catalog_is_valid_and_complete():
    records, report = build_catalog()
    # The six active data files are the complete authoritative catalog.
    assert len(records) == len(load_records()) == 105
    assert {r["line_type"] for r in records} == {"prepaid", "postpaid"}  # both kept
    assert {r["operator"] for r in records} == {"turkcell", "vodafone", "turk_telekom"}
    assert {r["customer_type"] for r in records} == {"new"}
    assert report.errors == []


def test_build_catalog_is_deterministic():
    first, _ = build_catalog()
    second, _ = build_catalog()
    assert [r["doc_text"] for r in first] == [r["doc_text"] for r in second]
    assert [package_id(r["operator"], r["line_type"], r["name"]) for r in first] == [
        package_id(r["operator"], r["line_type"], r["name"]) for r in second
    ]


def test_package_ids_are_stable_and_unique():
    records, _ = build_catalog()
    ids = {package_id(r["operator"], r["line_type"], r["name"]) for r in records}
    assert len(ids) == len(records)
    assert package_id("turkcell", "postpaid", "X") == package_id("turkcell", "postpaid", "X")


# ---- Verified questionable records ----

def test_d1_class_limited_plan_total_is_not_unlimited(raw):
    """D1 (Dijital Lider Prime 40 GB, now closed and removed) had total -1 with no
    unlimited component. Its current successor reads '40 GB internet' and stays 40."""
    rec = _by_name(raw, "Yeni Dijital Lider 40 GB")
    assert (rec["internet_gb"], rec["social_media_gb"], rec["bonus_internet_gb"]) == (40, None, None)
    assert "total_internet_gb" not in rec


def test_d1_original_defect_is_rejected(raw):
    """The defect class needed a stored total; a catalog record carrying one is refused."""
    rec = dict(_by_name(raw, "Yeni Dijital Lider 40 GB"), total_internet_gb=UNLIMITED)
    assert any("total_internet_gb is obsolete" in e for e in _errors_for(rec))


@pytest.mark.parametrize(
    "name", ["Memlekete Hoş Geldin 40GB Paketi", "Memlekete Hoş Geldin 80GB Paketi", "Memlekete Hoş Geldin 100GB Paketi"]
)
def test_d2_d3_memlekete_is_a_one_time_price(raw, name):
    """Official page: buyable once, 'tekrar etmez'. The price pays for one 90-day package
    (price_period); that it does not renew is renewal_type, not the period."""
    rec = _by_name(raw, name)
    assert rec["price_period"] == "90_day" and rec["renewal_type"] == "one_time"


@pytest.mark.parametrize("name", ["Memlekete Hoş Geldin 80GB Paketi", "Memlekete Hoş Geldin 100GB Paketi"])
def test_d2_original_monthly_period_is_rejected(raw, name):
    rec = dict(_by_name(raw, name), price_period="month")
    assert rec["validity_days"] == 90
    assert any("contradicts validity_days" in e for e in _errors_for(rec))


# ---- validation rules ----

def _base(**overrides):
    rec = {
        "operator": "turkcell", "line_type": "prepaid", "customer_type": "new",
        "name": "Test 20 GB", "package_group": "standard", "price_try": 200.0,
        "price_period": "28_day", "price_lock_months": None, "internet_gb": 20,
        "social_media_gb": None, "bonus_internet_gb": None,
        "voice_minutes": 1000, "sms_count": 250, "validity_value": 28, "validity_unit": "day",
        "validity_days": 28, "conditions": "Yeni hat alımlarında geçerlidir.", "campaign": None,
        "source_url": "https://example.com/p",
    }
    rec.update(overrides)
    return rec


def test_valid_base_record_passes():
    assert _errors_for(_base()) == []


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"internet_gb": -5}, "internet_gb=-5"),
        ({"internet_gb": None}, "internet_gb (general internet) is required"),
        ({"bonus_internet_gb": 5, "bonus_internet_scope": "hotspot"}, "bonus_internet_scope='hotspot'"),
        ({"price_try": 0}, "positive number"),
        ({"price_period": "90_day"}, "contradicts validity_days"),
        ({"renewal_type": "monthly"}, "renewal_type='monthly'"),
        ({"renewal_type": "one_time", "promo_months": 3, "price_after_promo_try": 300.0}, "one-time package"),
        ({"validity_days": None, "validity_value": None, "validity_unit": "day"}, "validity_days"),
        ({"voice_minutes": None}, "voice_minutes is required"),
        ({"sms_count": -2}, "sms_count=-2"),
        ({"source_url": "http://example.com"}, "https"),
    ],
)
def test_invalid_states_are_rejected(overrides, fragment):
    errors = _errors_for(_base(**overrides))
    assert any(fragment in e for e in errors), errors


def test_unlimited_social_does_not_make_general_internet_unlimited():
    rec = normalize_record(_base(social_media_gb=UNLIMITED))
    assert "sinirsiz genel internet" not in rec["doc_text"]
    assert "unlimited_data" not in benefit_tags(rec)
    assert _errors_for(_base(social_media_gb=UNLIMITED)) == []


def test_redundant_eligibility_tags_are_stripped_and_refused():
    rec = normalize_record(_base(eligibility_tags=["new_line", "number_porting", "age_restricted", "dealer_only"]))
    assert rec["eligibility_tags"] == ["dealer_only"]
    report = ValidationReport()
    validate_record({**rec, "eligibility_tags": ["new_line", "dealer_only"]}, report)
    assert any("eligibility_tags repeat ['new_line']" in e for e in report.errors)


def test_unlimited_general_internet_is_valid():
    assert _errors_for(_base(internet_gb=UNLIMITED)) == []


def test_pre_2d2_price_periods_are_refused_at_normalization():
    # 'one_time' / '3_month' mixed renewal and duration; they are gone, not silently mapped.
    for old in ("one_time", "3_month", "12_month"):
        with pytest.raises(ValueError, match="price_period"):
            normalize_record(_base(price_period=old))


def test_renewal_edge_cases():
    # 28-day prepaid with unknown renewal: valid (never guessed).
    assert _errors_for(_base(renewal_type="unknown")) == []
    # 90-day one-time package.
    assert _errors_for(_base(price_period="90_day", validity_value=90, validity_days=90, renewal_type="one_time")) == []
    # Automatically renewing prepaid package.
    assert _errors_for(_base(renewal_type="recurring")) == []
    # Monthly postpaid without commitment and without a stated usage validity.
    post = _base(line_type="postpaid", price_period="month", validity_value=None, validity_unit=None,
                 validity_days=None, commitment_status="no_commitment", commitment_months=None,
                 renewal_type="recurring")
    assert _errors_for(post) == []
    # Introductory price followed by a higher price, on a recurring plan.
    assert _errors_for({**post, "promo_months": 1, "price_after_promo_try": 300.0}) == []


def test_postpaid_rules():
    post = dict(
        line_type="postpaid", price_period="month", validity_value=1, validity_unit="month",
        validity_days=30, commitment_status="committed", commitment_months=12,
    )
    assert _errors_for(_base(**post)) == []
    assert any("must be 'month'" in e for e in _errors_for(_base(**{**post, "price_period": "90_day"})))
    assert any("commitment_months > 0" in e for e in _errors_for(_base(**{**post, "commitment_months": None})))
    assert any("must exceed" in e for e in _errors_for(_base(**{**post, "no_commitment_price_try": 100.0})))


def test_customer_type_outside_new_is_rejected():
    errors = _errors_for(_base(customer_type="existing"))
    assert any("new-customer only" in e for e in errors)


def test_unknown_operator_is_rejected():
    assert any("unknown operator" in e for e in _errors_for(_base(operator="turkcel")))


def test_duplicate_identity_in_one_bucket_is_rejected(raw):
    duplicated = raw + [copy.deepcopy(raw[0])]
    with pytest.raises(CatalogValidationError, match="duplicate identity"):
        build_catalog(duplicated)


def test_invalid_record_aborts_the_whole_catalog(raw):
    broken = copy.deepcopy(raw)
    broken[0]["total_internet_gb"] = 9999
    with pytest.raises(CatalogValidationError):
        build_catalog(broken)


# ---- evidence-based activation channels ----

def test_fee_disclosure_is_not_a_channel():
    rec = {"name": "Dijital Lider 1 GB", "conditions": (
        "Faturalı tarife aylık olarak ücretlendirilir. Aylık ücrete telsiz kullanım ücreti; "
        "yeni hat tesisinde telsiz ruhsatname ücreti ve özel iletişim vergisi dahil değildir."
    ), "campaign": None}
    assert activation_channels(rec) == []
    assert "kaynakta belirtilmemis" in channel_phrase([])


@pytest.mark.parametrize(
    "conditions, expected",
    [
        ("Numara taşıma ve yeni hat alımı için geçerlidir.", ["new_line", "number_porting"]),
        ("Yeni hat aktivasyonlarında geçerlidir. Yeni hat alımlarında SIM kart ücreti tahsil edilir.", ["new_line"]),
        ("Numarasını Turkcell'e taşıyan müşterilere sunulur.", ["number_porting"]),
        ("Bireysel faturasız müşteriler için 28 gün geçerlidir.", []),
    ],
)
def test_channels_come_from_eligibility_text(conditions, expected):
    assert activation_channels({"name": "X", "conditions": conditions}) == expected


def test_real_catalog_channels_never_come_from_fee_clauses_only():
    records, report = build_catalog()
    # The stored Türk Telekom postpaid texts mention "yeni hat" only in the fee clause, so
    # the text rule alone must find no channel there. Their channels come from the
    # tariff page's "Tarife Kapsamı" sentence, recorded explicitly.
    tt_postpaid = [r for r in records if r["operator"] == "turk_telekom" and r["line_type"] == "postpaid"]
    fee_only = [r for r in tt_postpaid if not r["name"].startswith("Yeni Dijital Lider")]
    assert fee_only and all(activation_channels(r) == [] for r in fee_only)
    assert all(r["activation_channels"] == ["new_line", "number_porting"] for r in tt_postpaid)
    # Unknown channels stay unknown: surfaced as warnings, never as errors or guesses.
    unknown = [r for r in records if r["activation_channels"] is None]
    # Dijital x4 were resolved from "yeni hat alarak veya numara taşıyarak gelen".
    assert {r["name"] for r in unknown} == {"Geniş 12 Ay 100 GB Paketi"}
    for rec in unknown:
        assert "kanali kaynakta belirtilmemis" in rec["doc_text"]
    assert len(report.warnings) == len(unknown) and all("activation channel" in w for w in report.warnings)
    # The doc text states both channels exactly when the record does.
    for rec in records:
        both = {"new_line", "number_porting"} <= set(rec["activation_channels"] or [])
        assert both == ("yeni hat veya numara tasima ile alinabilir" in rec["doc_text"]), rec["name"]


def test_new_customer_phrase_does_not_assert_a_channel():
    rec = normalize_record(_base(conditions="Bireysel faturasız müşteriler için geçerlidir."))
    assert "yalnizca operatore yeni gelen musterilere ozel" in rec["doc_text"]
    assert "numara tasima veya yeni hat ile gelenlere" not in rec["doc_text"]


# ---- embeddings ----

def test_embedding_validation():
    good = [[0.1] * 4, [0.2] * 4]
    assert validate_embeddings(good, 2, 4) == good
    with pytest.raises(ValueError, match="expected 3"):
        validate_embeddings(good, 3, 4)
    with pytest.raises(ValueError, match="dims"):
        validate_embeddings([[0.1] * 3, [0.2] * 4], 2, 4)
    with pytest.raises(ValueError, match="NaN"):
        validate_embeddings([[math.nan] * 4, [0.2] * 4], 2, 4)
    with pytest.raises(ValueError, match="all zeros"):
        validate_embeddings([[0.0] * 4, [0.2] * 4], 2, 4)


class _ExplodingEngine:
    """Any database use from run() is a test failure."""
    def __getattr__(self, item):
        raise AssertionError("database touched before embeddings were validated")


def test_embedding_failure_never_reaches_the_database():
    def failing_embed(texts):
        raise RuntimeError("model download failed")
    with pytest.raises(RuntimeError, match="model download"):
        run(engine=_ExplodingEngine(), embed=failing_embed)


def test_bad_vectors_never_reach_the_database():
    with pytest.raises(ValueError, match="dims"):
        run(engine=_ExplodingEngine(), embed=lambda texts: [[0.1] * 10 for _ in texts])


def test_validation_failure_never_embeds_or_touches_the_database(monkeypatch):
    def broken_records():
        recs = json.loads(json.dumps(load_records()))
        recs[0]["price_try"] = -1
        return recs
    monkeypatch.setattr(load_mod, "load_records", broken_records)
    embedded = []
    with pytest.raises(CatalogValidationError):
        run(engine=_ExplodingEngine(), embed=lambda texts: embedded.append(texts))
    assert embedded == []


# ---- verified current offers only ----

def _provenance_errors(rec: dict) -> list[str]:
    report = ValidationReport()
    validate_provenance(rec, report)
    return report.errors


_GOOD = {"operator": "turkcell", "line_type": "postpaid", "name": "X", "verified_at": "2026-10-12",
         "evidence": "Official page: 400 TL", "benefits": [], "benefits_status": "verified_none",
         "activation_channels": None, "renewal_type": "unknown"}


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"verified_at": None}, "verified_at"),
        ({"verified_at": "12.10.2026"}, "verified_at"),
        ({"evidence": ""}, "evidence"),
        ({"evidence": "Bu teklif müşteri alımına kapatılmıştır."}, "closure notice"),
        ({"evidence": "Kampanya Bitti"}, "closure notice"),
        ({"availability": "discontinued"}, "lifecycle field"),
        ({"closed_on": "2026-07-24"}, "lifecycle field"),
    ],
)
def test_provenance_rules(overrides, fragment):
    assert any(fragment in e for e in _provenance_errors({**_GOOD, **overrides}))


def test_good_provenance_passes():
    assert _provenance_errors(_GOOD) == []


def test_every_record_is_a_verified_current_offer(raw):
    for rec in raw:
        assert rec["verified_at"] == "2026-10-12", rec["name"]
        assert rec["evidence"].strip(), rec["name"]
        assert not set(rec) & {"availability", "availability_note", "closed_on", "superseded_by"}


def test_no_duplicate_active_plans_or_ids():
    records, _ = build_catalog()
    keys = [(r["operator"], r["line_type"], r["name"]) for r in records]
    assert len(keys) == len(set(keys))
    ids = {package_id(*k) for k in keys}
    assert len(ids) == len(records)
    # canonical_id is gone: `id` is the only identity.
    assert all("canonical_id" not in r for r in records)


def test_closed_offer_evidence_never_reaches_embedding(monkeypatch):
    recs = json.loads(json.dumps(load_records()))
    recs[0]["evidence"] = "Bu paket abone alımına kapalıdır"
    monkeypatch.setattr(load_mod, "load_records", lambda: recs)
    with pytest.raises(CatalogValidationError, match="closure notice"):
        run(engine=_ExplodingEngine(), embed=lambda texts: pytest.fail("embedded"))


@pytest.mark.parametrize(
    "name, price, old",
    [
        ("Hoş Geldin 50 GB Paketi", 300.0, 174.0),
        ("Geniş 12 Ay 100 GB Paketi", 3300.0, 3160.0),
        ("5G Ultra Dijital 5 GB", 400.0, 350.0),
        ("5G Online Plus 5GB Paketi", 315.0, 300.0),
        ("Yeni Nesil Prime 100 GB", 1200.0, 1050.0),  # relaunched under the same name
    ],
)
def test_verified_price_updates_keep_the_snapshot_value(raw, name, price, old):
    rec = _by_name(raw, name)
    assert rec["price_try"] == price
    assert rec["history"][0]["as_of"] == "2026-07-10" and rec["history"][0]["fields"]["price_try"] == old


def test_unchanged_records_have_no_history(raw):
    assert "history" not in _by_name(raw, "Yeni Parlamenter 5 GB")


def test_hos_geldin_50_is_new_line_only_per_source():
    records, _ = build_catalog()
    rec = next(r for r in records if r["name"] == "Hoş Geldin 50 GB Paketi")
    assert rec["activation_channels"] == ["new_line"]
    # The channel lives only in activation_channels.
    assert not {"new_line", "number_porting"} & set(rec["eligibility_tags"])


def test_memlekete_40_validity_is_90_days(raw):
    rec = _by_name(raw, "Memlekete Hoş Geldin 40GB Paketi")
    assert rec["validity_days"] == 90 and rec["price_period"] == "90_day" and rec["renewal_type"] == "one_time"


def test_new_youth_plans_carry_the_source_age_bounds():
    records, _ = build_catalog()
    gnc = [r for r in records if r["name"].startswith("5G GNÇ Avantaj")]
    assert len(gnc) == 7
    assert all((r["age_min"], r["age_max"], r["audience_segment"]) == (7, 28, "youth") for r in gnc)


def test_verified_at_is_the_stored_freshness_date():
    records, _ = build_catalog()
    assert {load_mod._captured_at(r).date().isoformat() for r in records} == {"2026-10-12"}
